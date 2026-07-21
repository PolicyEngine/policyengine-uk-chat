from types import SimpleNamespace

from engine import discovery
from engine.py_runtime import DatasetSpec


class ParameterCore:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def __call__(self, period):
        self.calls.append(period)
        if isinstance(period, int):
            raise ValueError("integer periods unsupported")
        return self.value


def fake_model():
    variables = {
        "employment_income": SimpleNamespace(
            label="Employment income",
            entity="person",
            description="Annual earnings",
            definition_period="year",
            value_type=float,
            default_value=0,
            possible_values=None,
        ),
        "rent": SimpleNamespace(
            label="Rent",
            entity="household",
            description=None,
            definition_period="month",
            value_type=None,
            default_value=0,
            possible_values=[0, 1],
        ),
        "internal": SimpleNamespace(entity=None),
    }
    parameter = SimpleNamespace(
        label="Personal allowance",
        description="Tax-free allowance",
        unit="GBP",
        _core_param=ParameterCore(12_570),
    )
    return SimpleNamespace(
        variables_by_name=variables,
        parameters_by_name={
            "gov.hmrc.income_tax.allowances.personal_allowance.amount": parameter,
        },
    )


def test_match_and_variable_discovery(monkeypatch):
    model = fake_model()
    monkeypatch.setattr(discovery, "uk_model_version", lambda: model)

    assert discovery._matches("", "anything") is True
    assert discovery._matches("earn", "Annual earnings") is True
    assert discovery._matches("employmnt income", "Employment income") is True
    assert discovery._matches("pension", "Employment income") is False

    entities = discovery.list_entities()["entities"]
    assert entities == [
        {"name": "person", "variable_count": 1},
        {"name": "household", "variable_count": 1},
    ]

    result = discovery.search_variables("earn", entity="person", limit=1)
    assert result["variables"][0]["name"] == "employment_income"
    assert result["variables"][0]["value_type"] == "float"
    assert discovery.search_variables(entity="household")["variables"][0]["name"] == "rent"


def test_get_variable_reports_details_and_suggestions(monkeypatch):
    model = fake_model()
    monkeypatch.setattr(discovery, "uk_model_version", lambda: model)

    assert discovery.get_variable("rent")["status"] == "success"
    missing = discovery.get_variable("employment_incom")
    assert missing["status"] == "error"
    assert "employment_income" in missing["suggestions"]


def test_parameter_discovery_resolves_year_and_aliases(monkeypatch):
    model = fake_model()
    monkeypatch.setattr(discovery, "uk_model_version", lambda: model)
    path = "gov.hmrc.income_tax.allowances.personal_allowance.amount"

    result = discovery.search_parameters("tax free", limit=1)
    assert result["parameters"][0]["path"] == path
    assert "personal allowance" in result["parameters"][0]["aliases"]

    parameter = discovery.get_parameter(path, 2026)["parameter"]
    assert parameter["value"] == 12_570
    assert parameter["year"] == 2026
    assert model.parameters_by_name[path]._core_param.calls == [2026, "2026"]

    missing = discovery.get_parameter(path[:-1], 2026)
    assert missing["status"] == "error"
    assert path in missing["suggestions"]
    assert discovery._parameter_value(SimpleNamespace(_core_param=None), 2026) is None


def test_dataset_reform_input_and_output_catalogs(monkeypatch):
    spec = DatasetSpec(
        name="example",
        label="Example",
        uri="hf://example",
        is_default=True,
        is_policyengine_standard_default=False,
        row_level_access=False,
    )
    monkeypatch.setattr(discovery, "list_dataset_specs", lambda: [spec])
    assert discovery.list_datasets()["datasets"][0]["name"] == "example"

    monkeypatch.setattr(
        discovery,
        "search_variables",
        lambda **kwargs: {"status": "success", "variables": [], **kwargs},
    )
    inputs = discovery.list_household_input_variables(entity="person")
    assert inputs["entity"] == "person"
    assert "synthetic-household override" in inputs["input_contract"]

    monkeypatch.setattr(
        discovery,
        "search_reform_targets",
        lambda **kwargs: [{"path": kwargs["query"]}],
    )
    targets = discovery.list_reform_targets("allowance", limit=2)
    assert targets["targets"] == [{"path": "allowance"}]

    assert len(discovery.supported_outputs()) > len(
        discovery.supported_outputs("artifact")
    )
    outputs = discovery.list_supported_outputs("household")
    assert outputs["status"] == "success"
    assert {row["scope"] for row in outputs["outputs"]} == {"household"}
