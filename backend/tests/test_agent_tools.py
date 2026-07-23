"""Tests for the model-facing UK policy tool lifecycle."""

from pathlib import Path

import tools.dispatch as agent_tools
from conftest import requires_policyengine_py
from engine import households as household_engine
from engine import simulations as simulation_engine
from engine.constants import (
    DEFAULT_UK_DATASET,
    DEFAULT_UK_DATASET_URI,
    HOUSEHOLD_COUNTRY_IDS,
    STANDARD_POLICYENGINE_UK_DATASET,
)
from engine.py_runtime import DatasetSpec
from engine.simulations import SocietySimulationRun
from tools.context import new_tool_context
from tools.definitions import DEFAULT_SIMULATION_YEAR, TOOL_DEFINITIONS


def _tool(name: str) -> dict:
    return next(tool for tool in TOOL_DEFINITIONS if tool["name"] == name)


def test_tool_inventory_matches_py_lifecycle():
    names = {tool["name"] for tool in TOOL_DEFINITIONS}
    assert {
        "list_datasets",
        "list_entities",
        "search_variables",
        "get_variable",
        "search_parameters",
        "get_parameter",
        "list_reform_targets",
        "list_household_input_variables",
        "list_society_output_variables",
        "list_supported_outputs",
        "validate_reform",
        "validate_household",
        "run_household_simulation",
        "run_society_simulation",
        "compute_budgetary_impact",
        "compute_program_breakdown",
        "compute_decile_impacts",
        "compute_winners_losers",
        "compute_poverty_metrics",
        "compute_inequality_metrics",
        "aggregate_result",
        "generate_chart",
    }.issubset(names)
    assert "run_python" not in names
    assert "calculate_household" not in names
    assert "run_economy_simulation" not in names
    assert "analyse_microdata" not in names
    assert "lookup_parameter" not in names


def test_default_year_and_dataset_are_py_migration_defaults():
    assert DEFAULT_SIMULATION_YEAR == 2026
    assert DEFAULT_UK_DATASET == "enhanced_frs_2024_25"
    assert DEFAULT_UK_DATASET_URI.endswith(
        "/enhanced_frs_2024_25.h5@1.56.13"
    )
    assert STANDARD_POLICYENGINE_UK_DATASET == "populace_uk_2023"
    society_schema = _tool("run_society_simulation")["input_schema"]
    assert society_schema["properties"]["year"]["default"] == 2026
    assert society_schema["properties"]["dataset"]["default"] == DEFAULT_UK_DATASET


def test_list_datasets_exposes_enhanced_frs_and_certified_standard():
    result = agent_tools.list_datasets()
    assert result["status"] == "success"
    by_name = {dataset["name"]: dataset for dataset in result["datasets"]}
    assert by_name[DEFAULT_UK_DATASET]["is_default"] is True
    assert by_name[STANDARD_POLICYENGINE_UK_DATASET]["is_policyengine_standard_default"] is True
    assert by_name[DEFAULT_UK_DATASET]["row_level_access"] is False


def test_discovery_tools_are_split_by_catalog_area():
    assert agent_tools.list_supported_outputs()["status"] == "success"
    society_outputs = agent_tools.list_society_output_variables(entity="household")
    assert society_outputs["status"] == "success"
    assert "household_net_income" in society_outputs[
        "default_variables_by_entity"
    ]["household"]
    targets = agent_tools.list_reform_targets(query="personal allowance")["targets"]
    assert any("personal_allowance" in target["path"] for target in targets)
    assert "input_only" not in _tool("search_variables")["input_schema"]["properties"]
    assert _tool("search_parameters")["input_schema"]["properties"]["query"]["type"] == "string"
    assert _tool("list_datasets")["input_schema"]["properties"] == {}


def test_run_household_simulation_passes_policyengine_py_shape_unchanged(monkeypatch):
    captured = {}

    def fake_calculate_household(**kwargs):
        captured.update(kwargs)
        return {"status": "success", "household_net_income": 25_000}

    monkeypatch.setattr(agent_tools, "calculate_household", fake_calculate_household)
    context = new_tool_context("test-session")
    result = agent_tools.execute_tool(
        "run_household_simulation",
        {
            "people": [{"age": 40, "employment_income": 30_000}],
            "benunit": {"is_married": False},
            "household": {"region": "LONDON"},
            "year": 2026,
        },
        context=context,
    )

    assert result["status"] == "success"
    assert result["result_id"].startswith("household_simulation_")
    assert captured["people"] == [{"age": 40, "employment_income": 30_000}]
    assert captured["benunit"] == {"is_married": False}
    assert captured["household"] == {"region": "LONDON"}
    assert captured["year"] == 2026


def test_household_country_schema_only_accepts_categorical_ids():
    household_schema = _tool("run_household_simulation")["input_schema"][
        "properties"
    ]["household"]

    assert household_schema["properties"]["country"]["enum"] == list(
        HOUSEHOLD_COUNTRY_IDS
    )
    assert household_schema["additionalProperties"] is True


def test_validate_household_rejects_non_categorical_country_values():
    for country in ("E92000001", "England", "england", "UNKNOWN"):
        result = household_engine.validate_household_dict(
            people=[{"age": 40}],
            benunit={},
            household={"country": country},
            year=2026,
        )

        assert result["valid"] is False
        assert result["errors"][0]["path"] == "household.country"
        assert "ENGLAND, NORTHERN_IRELAND, SCOTLAND, WALES" in result["errors"][0][
            "message"
        ]


def test_society_simulation_result_handle_feeds_derivative_and_chart_tools(monkeypatch):
    dataset = DatasetSpec(
        name=DEFAULT_UK_DATASET,
        label="Enhanced FRS 2024-25",
        uri="hf://policyengine/uk/enhanced_frs_2024_25",
        is_default=True,
        is_policyengine_standard_default=False,
        row_level_access=False,
    )
    payload = SocietySimulationRun(
        year=2026,
        dataset=dataset,
        reform_applied=True,
        reform={"gov.hmrc.vat.standard_rate": 0.21},
        baseline=object(),
        reform_simulation=object(),
    )

    monkeypatch.setattr(agent_tools, "build_society_simulation", lambda **_kwargs: payload)
    monkeypatch.setattr(
        agent_tools.derivatives,
        "budgetary_impact",
        lambda _payload: {
            "tax_revenue": {"baseline": 10, "reform": 1_000_000_010, "change": 1_000_000_000},
            "benefit_spending": {"baseline": 10, "reform": 250_000_010, "change": 250_000_000},
            "net_budgetary_impact": 750_000_000,
        },
    )
    context = new_tool_context("test-session")
    simulation = agent_tools.run_society_simulation(reform={"gov.hmrc.vat.standard_rate": 0.21}, _context=context)
    budget = agent_tools.compute_budgetary_impact(simulation["result_id"], _context=context)
    chart = agent_tools.generate_chart(
        chart_kind="budget_waterfall",
        result_id=budget["result_id"],
        _context=context,
    )

    assert simulation["result_id"].startswith("society_simulation_")
    assert "tax_revenue" not in simulation
    assert budget["net_budgetary_impact"] == 750_000_000
    assert chart["status"] == "success"
    assert chart["spec"]["type"] == "preset"
    assert chart["spec"]["preset"] == "budget_waterfall"
    assert "```chart" in chart["chart_markdown"]
    assert chart["spec"]["data"][-1]["value"] == 750_000_000


def test_society_simulation_passes_extra_variables(monkeypatch):
    captured = {}

    def fake_build(**kwargs):
        captured.update(kwargs)
        return SocietySimulationRun(
            year=kwargs["year"],
            dataset=DatasetSpec(
                name=DEFAULT_UK_DATASET,
                label="Enhanced FRS 2024-25",
                uri="hf://example",
                is_default=True,
                is_policyengine_standard_default=False,
                row_level_access=False,
            ),
            reform_applied=False,
            reform=None,
            baseline=object(),
            reform_simulation=object(),
        )

    monkeypatch.setattr(agent_tools, "build_society_simulation", fake_build)
    result = agent_tools.run_society_simulation(
        extra_variables={"household": ["rent"]},
        _context=new_tool_context("test-session"),
    )

    assert result["status"] == "success"
    assert captured["extra_variables"] == {"household": ["rent"]}


def test_society_simulation_normalizes_unset_reform_values(monkeypatch):
    captured = {}
    dataset = DatasetSpec(
        name=DEFAULT_UK_DATASET,
        label="Enhanced FRS 2024-25",
        uri="hf://example",
        is_default=True,
        is_policyengine_standard_default=False,
        row_level_access=False,
    )
    monkeypatch.setattr(simulation_engine, "resolve_dataset", lambda _name: dataset)

    def fake_pair(**kwargs):
        captured.update(kwargs)
        baseline = object()
        return baseline, baseline

    monkeypatch.setattr(simulation_engine, "managed_simulation_pair", fake_pair)

    result = simulation_engine.build_society_simulation(
        year=2026,
        reform={"gov.example": None},
    )

    assert captured["reform"] is None
    assert result.reform is None
    assert result.reform_applied is False


def test_household_simulation_does_not_run_empty_normalized_reform(monkeypatch):
    calls = []
    monkeypatch.setattr(
        household_engine,
        "validate_household_dict",
        lambda **_kwargs: {
            "valid": True,
            "normalized_reform": {},
        },
    )
    monkeypatch.setattr(
        household_engine,
        "calculate_household_py",
        lambda **kwargs: calls.append(kwargs) or {"household_net_income": 1},
    )

    result = household_engine.calculate_household(
        people=[{"age": 40}],
        benunit={},
        household={},
        year=2026,
        reform={"gov.example": None},
    )

    assert len(calls) == 1
    assert result["reform_applied"] is False
    assert "baseline" not in result


def test_society_runtime_never_aggregates_raw_arrays():
    root = Path(__file__).resolve().parents[1]
    files = [
        root / "engine" / "simulations.py",
        root / "engine" / "derivatives.py",
        root / "tools" / "dispatch.py",
    ]
    forbidden = (".calculate(", "np.asarray", "household_weight", "person_weight", "benunit_weight")
    hits = [
        f"{path.name}: {token}"
        for path in files
        for token in forbidden
        if token in path.read_text()
    ]
    assert hits == []


def test_aggregate_schema_only_exposes_official_weighted_operations():
    properties = _tool("aggregate_result")["input_schema"]["properties"]
    assert properties["operation"]["enum"] == ["sum", "mean", "count"]
    assert properties["target"]["enum"] == ["baseline", "reform", "change"]
    assert properties["filter_variable_geq"]["type"] == [
        "number",
        "string",
        "boolean",
    ]
    assert "group_by" not in properties


def test_aggregate_tool_forwards_official_filter_arguments(monkeypatch):
    context = new_tool_context("test-session")
    simulation_id = context.result_store.put(
        "society_simulation",
        object(),
        {"status": "success"},
    )
    captured = {}

    def fake_aggregate(_payload, **kwargs):
        captured.update(kwargs)
        return {"value": 1_234}

    monkeypatch.setattr(agent_tools.derivatives, "aggregate_result", fake_aggregate)

    result = agent_tools.aggregate_result(
        simulation_id=simulation_id,
        entity="household",
        variable="household_id",
        operation="count",
        filter_variable="benunit_count_children",
        filter_variable_geq=3,
        _context=context,
    )

    assert result["result"]["value"] == 1_234
    assert captured["filter_variable"] == "benunit_count_children"
    assert captured["filter_variable_geq"] == 3


def test_simulation_schema_has_no_executable_reform_escape_hatch():
    schema = _tool("run_society_simulation")["input_schema"]
    properties = schema["properties"]
    assert "structural_reform" not in properties
    assert "code" not in properties
    assert schema["additionalProperties"] is False
    assert properties["extra_variables"]["additionalProperties"] is False
    assert set(properties["extra_variables"]["properties"]) == {
        "person",
        "benunit",
        "household",
    }
    assert "invented names" in properties["extra_variables"]["description"]


def test_generate_chart_supports_explicit_generic_kinds():
    result = agent_tools.generate_chart(
        chart_kind="generic_bar",
        data=[{"decile": "1", "change": 10}],
        x_field="decile",
        y_fields=["change"],
        y_format="currency",
    )
    assert result["status"] == "success"
    assert result["spec"]["type"] == "bar"
    assert result["spec"]["y"]["format"] == "currency"


def test_program_waterfall_filters_zero_rows_and_appends_official_total():
    data = agent_tools._preset_chart_data(
        "program_budget_waterfall",
        {
            "programs": [
                {"program": "income_tax", "change": 10, "is_tax": True},
                {"program": "child_benefit", "change": 0, "is_tax": False},
                {"program": "universal_credit", "change": 4, "is_tax": False},
            ],
            "net_budgetary_impact": 6,
        },
    )

    assert data == [
        {"label": "Income Tax", "value": 10},
        {"label": "Universal Credit", "value": -4},
        {"label": "Total", "value": 6, "total": True},
    ]


def test_winners_losers_chart_keeps_official_overall_row():
    rows = [
        {"decile": 1, "no_change": 0.8},
        {"decile": 0, "no_change": 0.7},
    ]

    assert agent_tools._preset_chart_data(
        "winners_losers_stacked_bar",
        {"deciles": rows},
    ) == rows


def test_dispatch_rejects_removed_public_tool_names():
    for name in ("run_python", "calculate_household", "run_economy_simulation", "analyse_microdata"):
        result = agent_tools.execute_tool(name, {})
        assert result["error"] == f"Unknown tool: {name}"


def test_generate_chart_rejects_removed_generic_aliases():
    result = agent_tools.generate_chart(
        chart_kind="bar",
        data=[{"label": "A", "value": 1}],
        x_field="label",
        y_fields=["value"],
    )

    assert result == {"error": "Unknown chart kind: bar"}


def test_generate_chart_rejects_unadvertised_generic_kind():
    result = agent_tools.generate_chart(
        chart_kind="generic_pie",
        data=[{"label": "A", "value": 1}],
        x_field="label",
        y_fields=["value"],
    )

    assert result == {"error": "Unknown chart kind: generic_pie"}


def test_generate_chart_requires_nonempty_generic_data():
    result = agent_tools.generate_chart(
        chart_kind="generic_bar",
        x_field="label",
        y_fields=["value"],
    )

    assert result == {"error": "Generic chart data must contain at least one row."}


def test_runtime_files_do_not_reference_compiled_package():
    root = Path(__file__).resolve().parents[1]
    paths = [
        root / "api",
        root / "chat",
        root / "engine",
        root / "gateway",
        root / "prompts",
        root / "tools",
        root / "Dockerfile",
        root / ".dockerignore",
        root / "requirements.txt",
        root.parent / ".gitignore",
        root.parent / "modal_app.py",
    ]
    hits = []
    for path in paths:
        files = [path] if path.is_file() else path.rglob("*")
        for file in files:
            if not file.is_file() or file.suffix not in {"", ".py", ".txt"}:
                continue
            text = file.read_text(errors="ignore")
            if "policyengine_uk_compiled" in text or "policyengine-uk-compiled" in text:
                hits.append(str(file.relative_to(root.parent)))
    assert hits == []


@requires_policyengine_py
def test_validate_reform_uses_policyengine_py_when_available():
    result = agent_tools.validate_reform({"gov.hmrc.vat.standard_rate": 0.21}, year=2026)
    assert result["valid"] in {True, False}
    assert "reform_object" in result or "errors" in result
