"""
Integration tests for tools/dispatch.py.
These call the compiled PolicyEngine UK engine directly — no mocking.
Run inside the backend container: pytest tests/
"""

from types import SimpleNamespace

import pytest

from conftest import requires_compiled

import tools.dispatch as agent_tools
import tools.definitions as tool_definitions
from tools.dispatch import (
    get_baseline_parameters,
    validate_reform,
    calculate_household,
    run_economy_simulation,
    analyse_microdata,
    generate_chart,
    execute_tool,
    TOOL_DEFINITIONS,
    _build_compiled_policy,
    _json_safe,
    run_python,
)


def _tool(name: str) -> dict:
    return next(tool for tool in TOOL_DEFINITIONS if tool["name"] == name)


# ---------------------------------------------------------------------------
# policyengine_uk_compiled interface
# ---------------------------------------------------------------------------

@requires_compiled
class TestCompiledInterface:
    def test_simulation_single_person_available(self):
        from policyengine_uk_compiled import Simulation

        assert hasattr(Simulation, "single_person")
        assert callable(Simulation.single_person)


# ---------------------------------------------------------------------------
# get_baseline_parameters
# ---------------------------------------------------------------------------

@requires_compiled
class TestGetBaselineParameters:
    def test_returns_parameters(self):
        result = get_baseline_parameters(year=2023)
        assert "parameters" in result
        assert result["year"] == 2023

    def test_income_tax_present(self):
        result = get_baseline_parameters(year=2023)
        params = result["parameters"]
        assert "income_tax" in params

    def test_invalid_year_returns_error(self):
        result = get_baseline_parameters(year=9999)
        assert "error" in result


# ---------------------------------------------------------------------------
# calculate_household
# ---------------------------------------------------------------------------

SINGLE_ADULT = dict(
    person=[{"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 30000}],
    benunit=[{"benunit_id": 0, "household_id": 0}],
    household=[{"household_id": 0}],
    year=2023,
)

@requires_compiled
class TestCalculateHousehold:
    def test_basic_household(self):
        result = calculate_household(**SINGLE_ADULT)
        assert result["status"] == "success"
        assert len(result["person"]) == 1
        assert len(result["household"]) == 1

    def test_no_reform_uses_plain_income_tax(self):
        result = calculate_household(**SINGLE_ADULT)
        person = result["person"][0]
        assert person["income_tax"] > 0

    def test_reform_reduces_tax_with_higher_allowance(self):
        reform = {"income_tax": {"personal_allowance": 20000}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        person = result["person"][0]
        assert person["reform_income_tax"] < person["baseline_income_tax"]

    def test_reform_applied_flag(self):
        reform = {"income_tax": {"personal_allowance": 15000}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        assert result["reform_applied"] is True

    def test_no_reform_omits_comparison_columns(self):
        result = calculate_household(**SINGLE_ADULT)
        person = result["person"][0]
        benunit = result["benunit"][0]
        household = result["household"][0]
        assert "income_tax" in person
        assert "total_benefits" in benunit
        assert "net_income" in household
        assert "baseline_income_tax" not in person
        assert "reform_income_tax" not in person
        assert "baseline_total_benefits" not in benunit
        assert "reform_total_benefits" not in benunit
        assert "baseline_net_income" not in household
        assert "reform_net_income" not in household

    def test_empty_reform_uses_comparison_columns(self):
        result = calculate_household(**{**SINGLE_ADULT, "reform": {}})
        person = result["person"][0]
        household = result["household"][0]

        assert result["reform_applied"] is True
        assert "income_tax" not in person
        assert person["baseline_income_tax"] == pytest.approx(person["reform_income_tax"])
        assert household["baseline_net_income"] == pytest.approx(household["reform_net_income"])

    def test_batch_multiple_incomes(self):
        # IDs must be 0-indexed (engine constraint)
        persons = [{"person_id": i, "benunit_id": i, "household_id": i, "age": 35, "employment_income": (i + 1) * 10000} for i in range(5)]
        benunits = [{"benunit_id": i, "household_id": i} for i in range(5)]
        households = [{"household_id": i} for i in range(5)]
        result = calculate_household(person=persons, benunit=benunits, household=households, year=2023)
        assert result["status"] == "success"
        assert len(result["person"]) == 5

    def test_higher_income_pays_more_tax(self):
        persons = [
            {"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 20000},
            {"person_id": 1, "benunit_id": 1, "household_id": 1, "age": 35, "employment_income": 60000},
        ]
        benunits = [{"benunit_id": i, "household_id": i} for i in range(2)]
        households = [{"household_id": i} for i in range(2)]
        result = calculate_household(person=persons, benunit=benunits, household=households, year=2023)
        taxes = [p["income_tax"] for p in result["person"]]
        assert taxes[1] > taxes[0]

    def test_unknown_reform_program_returns_error(self):
        reform = {"not_a_program": {"some_field": 123}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        assert "error" in result

    def test_unknown_reform_field_returns_error(self):
        reform = {"income_tax": {"not_a_field": 123}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        assert "error" in result

    def test_uk_brackets_reform(self):
        # Raise higher rate to 50%
        reform = {"income_tax": {"uk_brackets": [
            {"rate": 0.20, "threshold": 0.0},
            {"rate": 0.50, "threshold": 37700.0},
            {"rate": 0.45, "threshold": 125140.0},
        ]}}
        high_earner = dict(
            person=[{"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 80000}],
            benunit=[{"benunit_id": 0, "household_id": 0}],
            household=[{"household_id": 0}],
            year=2023,
            reform=reform,
        )
        result = calculate_household(**high_earner)
        person = result["person"][0]
        assert person["reform_income_tax"] > person["baseline_income_tax"]

    def test_scotland_flag(self):
        result = calculate_household(
            person=[{"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 30000, "is_in_scotland": True}],
            benunit=[{"benunit_id": 0, "household_id": 0}],
            household=[{"household_id": 0}],
            year=2023,
        )
        assert result["status"] == "success"

    def test_zero_income(self):
        result = calculate_household(
            person=[{"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 0}],
            benunit=[{"benunit_id": 0, "household_id": 0}],
            household=[{"household_id": 0}],
            year=2023,
        )
        assert result["status"] == "success"
        assert result["person"][0]["income_tax"] == 0

    def test_universal_credit_low_income(self):
        result = calculate_household(
            person=[{"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 25, "employment_income": 8000}],
            benunit=[{"benunit_id": 0, "household_id": 0}],
            household=[{"household_id": 0}],
            year=2023,
        )
        assert result["status"] == "success"
        # Low earner should have some UC entitlement
        benunit = result["benunit"][0]
        assert "universal_credit" in benunit

    def test_historical_year(self):
        result = calculate_household(**{**SINGLE_ADULT, "year": 2010})
        assert result["status"] == "success"
        assert result["year"] == 2010

    def test_multi_person_household_with_children(self):
        # Multi-person household: adult + 2 children in same benunit
        result = calculate_household(
            person=[
                {"person_id": 0, "benunit_id": 0, "household_id": 0, "age": 35, "employment_income": 30000},
                {"person_id": 1, "benunit_id": 0, "household_id": 0, "age": 8},
                {"person_id": 2, "benunit_id": 0, "household_id": 0, "age": 5},
            ],
            benunit=[{"benunit_id": 0, "household_id": 0}],
            household=[{"household_id": 0}],
            year=2023,
        )
        assert result["status"] == "success"
        assert len(result["person"]) == 3
        assert len(result["benunit"]) == 1
        # Adult should pay income tax
        adult = result["person"][0]
        assert adult["income_tax"] > 0


# ---------------------------------------------------------------------------
# generate_chart
# ---------------------------------------------------------------------------

class TestGenerateChart:
    def test_line_chart(self):
        data = [{"x": i, "y": i * 2} for i in range(5)]
        result = generate_chart("line", "Test chart", data, "x", ["y"])
        assert result["status"] == "success"
        assert "```chart" in result["chart_markdown"]

    def test_bar_chart(self):
        data = [{"decile": i, "impact": i * 100} for i in range(1, 11)]
        result = generate_chart("bar", "Decile impacts", data, "decile", ["impact"])
        assert result["status"] == "success"

    def test_multi_series(self):
        data = [{"x": i, "baseline": i * 10, "reform": i * 12} for i in range(5)]
        result = generate_chart("line", "Baseline vs reform", data, "x", ["baseline", "reform"], series_labels=["Baseline", "Reform"])
        assert result["status"] == "success"
        import json
        spec = json.loads(result["chart_markdown"].replace("```chart\n", "").replace("\n```", ""))
        assert len(spec["series"]) == 2
        assert spec["series"][0]["label"] == "Baseline"

    def test_chart_markdown_format(self):
        data = [{"x": 1, "y": 2}]
        result = generate_chart("line", "T", data, "x", ["y"])
        assert result["chart_markdown"].startswith("```chart\n")
        assert result["chart_markdown"].endswith("\n```")

    def test_with_formats(self):
        data = [{"income": i * 10000, "tax": i * 2000} for i in range(10)]
        result = generate_chart("line", "Tax schedule", data, "income", ["tax"], x_format="currency", y_format="currency")
        assert result["status"] == "success"


class DummyModelDump:
    def model_dump(self):
        return {"child_benefit": 123}


class TestJsonSafe:
    def test_serialises_simulation_like_objects(self):
        serialised = _json_safe({"result": DummyModelDump()})
        assert serialised == {"result": {"child_benefit": 123}}


class TestToolDefinitions:
    def test_exposes_typed_tools_and_fallback_python(self):
        names = [tool["name"] for tool in TOOL_DEFINITIONS]
        assert "validate_reform" in names
        assert "calculate_household" in names
        assert "run_economy_simulation" in names
        assert "analyse_microdata" in names
        assert "run_python" in names

    def test_tool_definitions_match_dispatch_handlers(self):
        definition_names = [tool["name"] for tool in TOOL_DEFINITIONS]
        assert len(definition_names) == len(set(definition_names))
        assert set(definition_names) == set(agent_tools.TOOL_HANDLERS)

    def test_agent_tools_reexports_canonical_tool_definitions(self):
        assert TOOL_DEFINITIONS is tool_definitions.TOOL_DEFINITIONS

    def test_shared_schema_fragments_are_reused(self):
        household_schema = _tool("calculate_household")["input_schema"]
        economy_schema = _tool("run_economy_simulation")["input_schema"]
        microdata_schema = _tool("analyse_microdata")["input_schema"]
        chart_schema = _tool("generate_chart")["input_schema"]

        assert household_schema["properties"]["year"] is tool_definitions.YEAR_SCHEMA
        assert economy_schema["properties"]["year"] is tool_definitions.YEAR_SCHEMA
        assert microdata_schema["properties"]["year"] is tool_definitions.YEAR_SCHEMA
        assert economy_schema["properties"]["reform"] is tool_definitions.REFORM_PROPERTY
        assert microdata_schema["properties"]["reform"] is tool_definitions.REFORM_PROPERTY
        assert microdata_schema["properties"]["columns"] is tool_definitions.STRING_ARRAY_SCHEMA
        assert chart_schema["properties"]["x_format"]["enum"] == tool_definitions.CHART_FORMAT_SCHEMA["enum"]

    def test_validate_reform_tool_is_debugging_tool(self):
        description = _tool("validate_reform")["description"]
        assert "without running a simulation" in description
        assert "drafting" in description
        assert "routine preflight" in description

    def test_analyse_microdata_schema_excludes_frs(self):
        dataset_schema = _tool("analyse_microdata")["input_schema"]["properties"]["dataset"]
        assert dataset_schema["default"] == "efrs"
        assert "frs" not in dataset_schema["enum"]
        assert "efrs" in dataset_schema["enum"]

    def test_analyse_microdata_guidance_blocks_efrs_sample(self):
        tool = _tool("analyse_microdata")
        operation_schema = tool["input_schema"]["properties"]["operation"]
        assert "efrs" in operation_schema["description"]
        assert "efrs" in tool["description"]

    def test_run_economy_simulation_schema_has_no_structural_reform(self):
        properties = _tool("run_economy_simulation")["input_schema"]["properties"]
        assert "structural_reform" not in properties
        assert "structural_reform" not in _tool("analyse_microdata")["input_schema"]["properties"]

    def test_run_python_is_described_as_fallback(self):
        description = _tool("run_python")["description"]
        assert "fallback" in description.lower()
        assert "calculate_household" in description
        assert "run_economy_simulation" in description
        assert "analyse_microdata" in description


class TestAnalyseMicrodataContract:
    @pytest.mark.parametrize("dataset", ["frs", "FRS"])
    def test_rejects_frs_before_loading_microdata(self, monkeypatch, dataset):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("FRS rejection must happen before loading or building simulations")

        monkeypatch.setattr(agent_tools, "_get_cached_microdata", fail_if_called)
        monkeypatch.setattr(agent_tools, "_build_simulation", fail_if_called)
        monkeypatch.setattr(agent_tools, "_build_compiled_policy", fail_if_called)

        result = analyse_microdata(entity="households", operation="count", dataset=dataset)
        assert "error" in result
        assert "FRS" in result["error"]

    @pytest.mark.parametrize("dataset", ["efrs", "EFRS"])
    def test_rejects_efrs_sample_before_loading_microdata(self, monkeypatch, dataset):
        def fail_if_called(*args, **kwargs):
            raise AssertionError("EFRS sample rejection must happen before loading or building simulations")

        monkeypatch.setattr(agent_tools, "_get_cached_microdata", fail_if_called)
        monkeypatch.setattr(agent_tools, "_build_simulation", fail_if_called)
        monkeypatch.setattr(agent_tools, "_build_compiled_policy", fail_if_called)

        result = analyse_microdata(entity="households", operation="sample", dataset=dataset)
        assert "error" in result
        assert "Enhanced FRS" in result["error"]
        assert "aggregate" in result["hint"]

    def _install_mock_microdata(self, monkeypatch, row_count=4, comparison=False):
        import pandas as pd

        household_ids = list(range(row_count))
        regions = ["North" if i % 2 == 0 else "South" for i in household_ids]
        weights = [2.0, 1.0, 3.0, 4.0] if row_count == 4 else [1.0] * row_count
        incomes = [10.0, 30.0, 20.0, 40.0] if row_count == 4 else [float(i) for i in household_ids]

        household_data = {
            "household_id": household_ids,
            "region": regions,
            "weight": weights,
        }
        person_data = {
            "person_id": household_ids,
            "household_id": household_ids,
            "age": [30 + i for i in household_ids],
            "gender": ["F" if i % 2 == 0 else "M" for i in household_ids],
            "employment_income": incomes,
        }
        benunit_data = {
            "benunit_id": household_ids,
            "household_id": household_ids,
        }
        if comparison:
            household_data.update(
                {
                    "baseline_net_income": incomes,
                    "reform_net_income": [income + 5.0 for income in incomes],
                    "baseline_total_tax": [income / 10.0 for income in incomes],
                    "reform_total_tax": [income / 10.0 + 1.0 for income in incomes],
                    "baseline_total_benefits": [1.0 for _ in household_ids],
                    "reform_total_benefits": [2.0 for _ in household_ids],
                }
            )
            person_data.update(
                {
                    "baseline_income_tax": [income / 10.0 for income in incomes],
                    "reform_income_tax": [income / 10.0 + 1.0 for income in incomes],
                    "baseline_total_income": incomes,
                    "reform_total_income": [income + 5.0 for income in incomes],
                }
            )
            benunit_data.update(
                {
                    "baseline_total_benefits": [1.0 for _ in household_ids],
                    "reform_total_benefits": [2.0 for _ in household_ids],
                }
            )
        else:
            household_data.update(
                {
                    "net_income": incomes,
                    "total_tax": [income / 10.0 for income in incomes],
                    "total_benefits": [1.0 for _ in household_ids],
                }
            )
            person_data.update(
                {
                    "income_tax": [income / 10.0 for income in incomes],
                    "total_income": incomes,
                }
            )
            benunit_data.update(
                {
                    "total_benefits": [1.0 for _ in household_ids],
                    "universal_credit": [0.0 for _ in household_ids],
                }
            )

        households = pd.DataFrame(household_data)
        persons = pd.DataFrame(person_data)
        benunits = pd.DataFrame(benunit_data)
        microdata = SimpleNamespace(persons=persons, benunits=benunits, households=households)

        calls = []

        def get_cached_microdata(year, reform, dataset):
            calls.append({"year": year, "reform": reform, "dataset": dataset})
            return microdata

        monkeypatch.setattr(agent_tools, "_get_cached_microdata", get_cached_microdata)
        return calls

    def test_count_uses_weights_after_filtering(self, monkeypatch):
        self._install_mock_microdata(monkeypatch)

        result = analyse_microdata(
            entity="households",
            operation="count",
            filters={"region": "North"},
            dataset="efrs",
        )

        assert result["row_count"] == 2
        assert result["result"] == {"row_count": 2, "weighted_population": 5}

    def test_mean_uses_weights(self, monkeypatch):
        self._install_mock_microdata(monkeypatch)

        result = analyse_microdata(
            entity="households",
            operation="mean",
            columns=["net_income"],
            dataset="efrs",
        )

        assert result["result"]["net_income"] == pytest.approx(27.0)

    def test_group_by_returns_weighted_group_means(self, monkeypatch):
        self._install_mock_microdata(monkeypatch)

        result = analyse_microdata(
            entity="households",
            operation="group_by",
            columns=["net_income"],
            group_by=["region"],
            dataset="efrs",
        )

        rows = {row["region"]: row for row in result["result"]}
        assert rows["North"]["row_count"] == 2
        assert rows["North"]["weighted_population"] == 5.0
        assert rows["North"]["net_income"] == pytest.approx(16.0)
        assert rows["South"]["row_count"] == 2
        assert rows["South"]["weighted_population"] == 5.0
        assert rows["South"]["net_income"] == pytest.approx(38.0)

    def test_sample_is_deterministic_and_capped_for_non_frs(self, monkeypatch):
        self._install_mock_microdata(monkeypatch, row_count=25)

        kwargs = {
            "entity": "households",
            "operation": "sample",
            "columns": ["household_id", "net_income"],
            "n": 100,
            "dataset": "spi",
        }
        first = analyse_microdata(**kwargs)
        second = analyse_microdata(**kwargs)

        assert len(first["result"]) == 20
        assert first["result"] == second["result"]

    def test_no_reform_defaults_use_plain_columns(self, monkeypatch):
        self._install_mock_microdata(monkeypatch)

        result = analyse_microdata(entity="households", operation="mean", dataset="efrs")

        assert result["result"]["net_income"] == pytest.approx(27.0)
        assert result["result"]["total_tax"] == pytest.approx(2.7)
        assert "baseline_net_income" not in result["result"]
        assert "reform_net_income" not in result["result"]

    def test_reform_defaults_use_comparison_columns(self, monkeypatch):
        self._install_mock_microdata(monkeypatch, comparison=True)

        result = analyse_microdata(
            entity="households",
            operation="mean",
            reform={"income_tax": {"personal_allowance": 15000}},
            dataset="efrs",
        )

        assert result["result"]["baseline_net_income"] == pytest.approx(27.0)
        assert result["result"]["reform_net_income"] == pytest.approx(32.0)
        assert result["result"]["net_income_change"] == pytest.approx(5.0)
        assert "net_income" not in result["result"]

    def test_empty_reform_defaults_use_comparison_columns(self, monkeypatch):
        self._install_mock_microdata(monkeypatch, comparison=True)

        result = analyse_microdata(entity="households", operation="mean", reform={}, dataset="efrs")

        assert result["reform_applied"] is True
        assert result["result"]["baseline_net_income"] == pytest.approx(27.0)
        assert result["result"]["reform_net_income"] == pytest.approx(32.0)
        assert result["result"]["net_income_change"] == pytest.approx(5.0)


class _DummyDump:
    def __init__(self, value):
        self.value = value

    def model_dump(self):
        return self.value


def _economy_result(hbai_mean, breakdown):
    return SimpleNamespace(
        fiscal_year="2025/26",
        program_breakdown=_DummyDump(breakdown),
        budgetary_impact=_DummyDump({"baseline_revenue": 1.0}),
        decile_impacts=[],
        winners_losers=_DummyDump({"winners_pct": 0.0}),
        caseloads=_DummyDump({"income_tax_payers": 0.0}),
        baseline_hbai_incomes=_DummyDump({"mean_bhc": hbai_mean}),
        reform_hbai_incomes=_DummyDump({"mean_bhc": hbai_mean}),
        baseline_poverty=_DummyDump({"relative_bhc_children": 10.0}),
        reform_poverty=_DummyDump({"relative_bhc_children": 10.0}),
    )


class TestRunEconomySimulationContract:
    def test_reports_true_baseline_hbai_alongside_reform(self, monkeypatch):
        baseline = _economy_result(100.0, {"income_tax": 100.0})
        reform = _economy_result(80.0, {"income_tax": 90.0})

        class DummySimulation:
            def __init__(self):
                self.calls = 0

            def run(self, policy=None):
                self.calls += 1
                return baseline if self.calls == 1 else reform

        monkeypatch.setattr(agent_tools, "_build_simulation", lambda year, dataset: DummySimulation())
        monkeypatch.setattr(agent_tools, "_build_compiled_policy", lambda reform: object())

        result = agent_tools.run_economy_simulation(
            year=2025,
            reform={"income_tax": {"personal_allowance": 15000}},
        )

        assert result["baseline_hbai_incomes"]["mean_bhc"] == 100.0
        assert result["reform_hbai_incomes"]["mean_bhc"] == 80.0
        assert result["program_breakdown_changes"]["income_tax"]["change"] == -10.0
        assert "structural_reform_applied" not in result

    @requires_compiled
    def test_empty_reform_runs_reform_policy_path(self, monkeypatch):
        baseline = _economy_result(100.0, {"income_tax": 100.0})
        reform = _economy_result(100.0, {"income_tax": 100.0})
        policies = []

        class DummySimulation:
            def run(self, policy=None):
                policies.append(policy)
                return baseline if len(policies) == 1 else reform

        monkeypatch.setattr(agent_tools, "_build_simulation", lambda year, dataset: DummySimulation())

        result = agent_tools.run_economy_simulation(year=2025, reform={})

        assert result["program_breakdown_changes"]["income_tax"]["change"] == 0.0
        assert len(policies) == 2
        assert policies[0] is None
        assert policies[1] is not None

    def test_rejects_structural_reform_input(self):
        result = execute_tool(
            "run_economy_simulation",
            {"structural_reform": {"pre": "def hook(*args): return args"}},
        )
        assert "error" in result
        assert "structural_reform" in result["error"]


# ---------------------------------------------------------------------------
# validate_reform
# ---------------------------------------------------------------------------

class TestValidateReform:
    @pytest.fixture(autouse=True)
    def mock_parameter_classes(self, monkeypatch):
        import engine.reforms as reform_helpers

        class DummyIncomeTaxParams:
            model_fields = {"personal_allowance": None, "higher_rate": None}

            def __init__(self, **kwargs):
                self.kwargs = kwargs

        monkeypatch.setattr(
            reform_helpers,
            "_parameter_classes",
            lambda: ({"income_tax": DummyIncomeTaxParams}, object, object),
        )

    def test_valid_reform_returns_normalized_reform(self):
        result = validate_reform({"income_tax": {"personal_allowance": 15000}})

        assert result["valid"] is True
        assert result["normalized_reform"] == {"income_tax": {"personal_allowance": 15000}}
        assert result["programs"] == ["income_tax"]
        assert result["warnings"] == []

    def test_unknown_program_returns_json_error(self):
        result = validate_reform({"not_real": {"field": 1}})

        assert result["valid"] is False
        assert result["errors"][0]["path"] == "not_real"
        assert "Unknown reform program" in result["errors"][0]["message"]
        assert result["valid_programs"] == ["income_tax"]

    def test_unknown_field_returns_json_error(self):
        result = validate_reform({"income_tax": {"not_real_field": 1}})

        assert result["valid"] is False
        assert result["errors"][0]["path"] == "income_tax.not_real_field"
        assert "Unknown field" in result["errors"][0]["message"]

    def test_null_fields_are_stripped(self):
        result = validate_reform(
            {"income_tax": {"personal_allowance": 15000, "higher_rate": None}}
        )

        assert result["valid"] is True
        assert result["normalized_reform"] == {"income_tax": {"personal_allowance": 15000}}

    def test_simulation_and_validator_share_invalid_reform_rules(self):
        reform = {"income_tax": {"not_real_field": 1}}

        validation = validate_reform(reform)
        simulation = run_economy_simulation(reform=reform)

        assert validation["valid"] is False
        assert "Unknown field" in validation["errors"][0]["message"]
        assert "error" in simulation
        assert "Unknown field" in simulation["error"]


@requires_compiled
class TestValidateReformCompiledPath:
    def test_valid_reform_uses_compiled_parameter_classes(self):
        result = validate_reform({"income_tax": {"personal_allowance": 15000}})

        assert result["valid"] is True
        assert result["normalized_reform"] == {"income_tax": {"personal_allowance": 15000}}

    def test_valid_programs_match_compiled_parameter_classes(self):
        from engine.reforms import _parameter_classes, get_valid_programs

        param_cls_map, _, _ = _parameter_classes()

        assert get_valid_programs() == list(param_cls_map)
        result = validate_reform({"not_real": {"field": 1}})
        assert result["valid"] is False
        assert result["valid_programs"] == list(param_cls_map)


@requires_compiled
class TestRunPython:
    def test_replaces_old_compute_sum_use_case(self):
        result = run_python("data = [10, 20, 30]\nresult = sum(data)")
        assert result["result"] == 60

    def test_replaces_old_compute_marginal_rate_use_case(self):
        code = """
net = [8000, 14800, 21600]
gross = [10000, 20000, 30000]
result = [
    (net[i + 1] - net[i]) / (gross[i + 1] - gross[i]) * 100
    for i in range(len(net) - 1)
]
"""
        result = run_python(code)
        assert abs(result["result"][0] - 68.0) < 0.01

    def test_supports_basic_introspection(self):
        result = run_python("value = {'a': 1}\nresult = {'type': type(value).__name__, 'dir_has_keys': 'keys' in dir(value)}")
        assert result["result"] == {"type": "dict", "dir_has_keys": True}

    def test_supports_safe_imports(self):
        result = run_python("import json\nresult = json.loads('{\"ok\": true}')")
        assert result["result"] == {"ok": True}

    def test_rejects_frs_row_level_microdata(self):
        result = run_python("sim = Simulation(year=2025)\nresult = sim.run_microdata()")

        assert "error" in result
        assert "FRS row-level microdata" in result["error"]

    def test_rejects_compiled_module_bypass_for_frs_microdata(self):
        result = run_python("result = getattr(pe, '_module')")

        assert "error" in result
        assert "AttributeError" in result["error"]

    def test_allows_synthetic_microdata(self):
        code = """
persons, benunits, households = Simulation.single_person(employment_income=30000)
sim = Simulation(year=2025, persons=persons, benunits=benunits, households=households)
md = sim.run_microdata()
result = {"persons": len(md.persons), "households": len(md.households)}
"""
        result = run_python(code)

        assert result["result"] == {"persons": 1, "households": 1}


# ---------------------------------------------------------------------------
# _build_compiled_policy
# ---------------------------------------------------------------------------

@requires_compiled
class TestBuildCompiledPolicy:
    def test_none_reform_returns_none(self):
        assert _build_compiled_policy(None) is None

    def test_empty_reform_returns_empty_policy(self):
        # Mirrors policyengine-uk-compiled: supplying even an empty Parameters
        # object puts run_microdata in baseline_*/reform_* comparison mode.
        assert _build_compiled_policy({}) is not None

    def test_valid_reform(self):
        policy = _build_compiled_policy({"income_tax": {"personal_allowance": 15000}})
        assert policy is not None

    def test_falsy_non_dict_reform_raises(self):
        with pytest.raises(ValueError, match="Reform must be a dict"):
            _build_compiled_policy([])

    def test_unknown_program_raises(self):
        with pytest.raises(ValueError, match="Unknown reform program"):
            _build_compiled_policy({"not_real": {"field": 1}})

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="Unknown field"):
            _build_compiled_policy({"income_tax": {"not_real_field": 1}})


class TestReformCacheKeys:
    def test_empty_reform_hash_differs_from_absent_reform(self):
        assert agent_tools._hash_reform({}) != agent_tools._hash_reform(None)


# ---------------------------------------------------------------------------
# execute_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def test_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result

    @requires_compiled
    def test_dispatches_run_python(self):
        result = execute_tool("run_python", {"code": "result = sum([1, 2, 3])"})
        assert result["result"] == 6

    def test_compute_is_not_exposed(self):
        result = execute_tool("compute", {"operation": "sum", "data": [1, 2, 3]})
        assert result["error"] == "Unknown tool: compute"

    def test_dispatches_validate_reform(self, monkeypatch):
        monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "validate_reform", lambda **kwargs: {"tool": "validator", "input": kwargs})
        result = execute_tool("validate_reform", {"reform": {}})
        assert result["tool"] == "validator"

    def test_dispatches_calculate_household(self, monkeypatch):
        monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "calculate_household", lambda **kwargs: {"tool": "household", "input": kwargs})
        result = execute_tool("calculate_household", {"person": [], "benunit": [], "household": []})
        assert result["tool"] == "household"

    def test_dispatches_run_economy_simulation(self, monkeypatch):
        monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "run_economy_simulation", lambda **kwargs: {"tool": "economy", "input": kwargs})
        result = execute_tool("run_economy_simulation", {"year": 2025})
        assert result["tool"] == "economy"

    def test_dispatches_analyse_microdata(self, monkeypatch):
        monkeypatch.setitem(agent_tools.TOOL_HANDLERS, "analyse_microdata", lambda **kwargs: {"tool": "microdata", "input": kwargs})
        result = execute_tool("analyse_microdata", {"entity": "households", "operation": "count"})
        assert result["tool"] == "microdata"

    def test_dispatches_generate_chart(self):
        result = execute_tool("generate_chart", {
            "chart_type": "line", "title": "T",
            "data": [{"x": 1, "y": 2}], "x_field": "x", "y_fields": ["y"],
        })
        assert result["status"] == "success"
