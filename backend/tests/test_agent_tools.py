"""
Integration tests for agent_tools.py.
These call the compiled PolicyEngine UK engine directly — no mocking.
Run inside the backend container: pytest tests/
"""

import pytest
from agent_tools import (
    get_baseline_parameters,
    calculate_household,
    compute,
    generate_chart,
    execute_tool,
    _build_compiled_policy,
)


# ---------------------------------------------------------------------------
# get_baseline_parameters
# ---------------------------------------------------------------------------

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

class TestCalculateHousehold:
    def test_basic_household(self):
        result = calculate_household(**SINGLE_ADULT)
        assert result["status"] == "success"
        assert len(result["person"]) == 1
        assert len(result["household"]) == 1

    def test_baseline_income_tax_positive(self):
        result = calculate_household(**SINGLE_ADULT)
        person = result["person"][0]
        assert person["baseline_income_tax"] > 0

    def test_reform_reduces_tax_with_higher_allowance(self):
        reform = {"income_tax": {"personal_allowance": 20000}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        person = result["person"][0]
        assert person["reform_income_tax"] < person["baseline_income_tax"]

    def test_reform_applied_flag(self):
        reform = {"income_tax": {"personal_allowance": 15000}}
        result = calculate_household(**{**SINGLE_ADULT, "reform": reform})
        assert result["reform_applied"] is True

    def test_no_reform_baseline_equals_reform(self):
        result = calculate_household(**SINGLE_ADULT)
        person = result["person"][0]
        assert person["baseline_income_tax"] == person["reform_income_tax"]

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
        taxes = [p["baseline_income_tax"] for p in result["person"]]
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
        assert result["person"][0]["baseline_income_tax"] == 0

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
        assert "baseline_universal_credit" in benunit

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
        assert adult["baseline_income_tax"] > 0


# ---------------------------------------------------------------------------
# compute
# ---------------------------------------------------------------------------

class TestCompute:
    def test_diff(self):
        result = compute("diff", [1, 3, 6, 10])
        assert result["result"] == [2, 3, 4]

    def test_pct_change(self):
        result = compute("pct_change", [100, 110])
        assert abs(result["result"][0] - 10.0) < 0.001

    def test_mean(self):
        result = compute("mean", [1, 2, 3, 4, 5])
        assert result["result"] == 3.0

    def test_sum(self):
        result = compute("sum", [10, 20, 30])
        assert result["result"] == 60

    def test_marginal_rate(self):
        # net incomes at £10k steps, gross incomes
        net = [8000, 14800, 21600]
        gross = [10000, 20000, 30000]
        result = compute("marginal_rate", net, gross)
        # (14800-8000)/(20000-10000)*100 = 68%
        assert abs(result["result"][0] - 68.0) < 0.01

    def test_subtract(self):
        result = compute("subtract", [10, 20, 30], [1, 2, 3])
        assert result["result"] == [9, 18, 27]

    def test_divide_by_zero(self):
        result = compute("divide", [10, 20], [0, 4])
        assert result["result"][0] == 0  # safe division

    def test_empty_data(self):
        result = compute("sum", [])
        assert "error" in result

    def test_unknown_operation(self):
        result = compute("nonexistent", [1, 2, 3])
        assert "error" in result

    def test_mismatched_lengths(self):
        result = compute("subtract", [1, 2, 3], [1, 2])
        assert "error" in result


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


# ---------------------------------------------------------------------------
# _build_compiled_policy
# ---------------------------------------------------------------------------

class TestBuildCompiledPolicy:
    def test_none_reform_returns_none(self):
        assert _build_compiled_policy(None) is None

    def test_empty_reform_returns_none(self):
        assert _build_compiled_policy({}) is None

    def test_valid_reform(self):
        policy = _build_compiled_policy({"income_tax": {"personal_allowance": 15000}})
        assert policy is not None

    def test_unknown_program_raises(self):
        with pytest.raises(ValueError, match="Unknown reform program"):
            _build_compiled_policy({"not_real": {"field": 1}})

    def test_unknown_field_raises(self):
        with pytest.raises(ValueError, match="Unknown field"):
            _build_compiled_policy({"income_tax": {"not_real_field": 1}})


# ---------------------------------------------------------------------------
# execute_tool dispatcher
# ---------------------------------------------------------------------------

class TestExecuteTool:
    def test_unknown_tool(self):
        result = execute_tool("nonexistent_tool", {})
        assert "error" in result

    def test_dispatches_compute(self):
        result = execute_tool("compute", {"operation": "sum", "data": [1, 2, 3]})
        assert result["result"] == 6

    def test_dispatches_generate_chart(self):
        result = execute_tool("generate_chart", {
            "chart_type": "line", "title": "T",
            "data": [{"x": 1, "y": 2}], "x_field": "x", "y_fields": ["y"],
        })
        assert result["status"] == "success"
