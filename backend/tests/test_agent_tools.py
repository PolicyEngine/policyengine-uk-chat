"""Tests for the model-facing UK policy tool lifecycle."""

import inspect
from pathlib import Path

import tools.dispatch as agent_tools
from conftest import requires_policyengine_py
from engine import households as household_engine
from engine import simulations as simulation_engine
from engine.constants import (
    HOUSEHOLD_COUNTRY_IDS,
    UK_CHAT_DATASET,
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
    assert "list_datasets" not in names


def test_simulation_schema_uses_fixed_dataset():
    assert DEFAULT_SIMULATION_YEAR == 2026
    assert UK_CHAT_DATASET.name == "enhanced_frs_2024_25"
    assert UK_CHAT_DATASET.label == "Enhanced FRS 2024-25"
    assert UK_CHAT_DATASET.uri.startswith("hf://")
    assert "@" in UK_CHAT_DATASET.uri
    society_schema = _tool("run_society_simulation")["input_schema"]
    assert society_schema["properties"]["year"]["default"] == 2026
    assert "dataset" not in society_schema["properties"]
    assert "dataset" not in inspect.signature(
        agent_tools.run_society_simulation
    ).parameters
    assert "dataset" not in inspect.signature(
        simulation_engine.build_society_simulation
    ).parameters


@requires_policyengine_py
def test_reform_target_discovery_finds_capital_gains_tax():
    targets = agent_tools.list_reform_targets(query="capital gains tax")["targets"]

    assert any(target["path"] == "gov.hmrc.cgt.basic_rate" for target in targets)


def test_decile_tool_exposes_three_state_concept():
    tool = _tool("compute_decile_impacts")
    properties = tool["input_schema"]["properties"]

    assert set(properties) == {"simulation_id", "decile_concept"}
    assert properties["decile_concept"] == {
        "type": "string",
        "enum": [
            "household_net_income",
            "equivalised_hbai_net_income",
            "wealth",
        ],
        "default": "household_net_income",
        "description": properties["decile_concept"]["description"],
    }
    assert "Select exactly one" in properties["decile_concept"]["description"]
    assert "explicitly requests" in properties["decile_concept"]["description"]
    assert "equivalised HBAI net income" in tool["description"]
    assert "Wealth deciles" in tool["description"]
    assert "exclude negative or non-finite" in tool["description"]
    assert "null impacts, not zero" in tool["description"]


def test_decile_tool_passes_explicit_decile_concept_to_derivative(monkeypatch):
    captured = {}

    def fake_decile_impacts(payload, **kwargs):
        captured["payload"] = payload
        captured.update(kwargs)
        return {
            "decile_concept": kwargs["decile_concept"],
            "basis": "income",
            "income_variable": "equiv_hbai_household_net_income",
            "decile_variable": None,
            "entity": "household",
            "deciles": [],
        }

    monkeypatch.setattr(
        agent_tools.derivatives,
        "decile_impacts",
        fake_decile_impacts,
    )
    context = new_tool_context("decile-income-concept")
    payload = object()
    simulation_id = context.result_store.put(
        "society_simulation",
        payload,
        {},
    )

    result = agent_tools.compute_decile_impacts(
        simulation_id,
        decile_concept="equivalised_hbai_net_income",
        _context=context,
    )

    assert captured == {
        "payload": payload,
        "decile_concept": "equivalised_hbai_net_income",
    }
    assert result["income_variable"] == "equiv_hbai_household_net_income"


def test_decile_tool_rejects_concepts_outside_three_states():
    context = new_tool_context("invalid-decile-concept")
    simulation_id = context.result_store.put(
        "society_simulation",
        object(),
        {},
    )

    result = agent_tools.execute_tool(
        "compute_decile_impacts",
        {
            "simulation_id": simulation_id,
            "decile_concept": "household_tax",
        },
        context=context,
    )

    assert result == {
        "error": (
            "Unknown decile_concept 'household_tax'; expected one of: "
            "household_net_income, equivalised_hbai_net_income, wealth."
        )
    }


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
        name=UK_CHAT_DATASET.name,
        label="Enhanced FRS 2024-25",
        uri="hf://policyengine/uk/enhanced_frs_2024_25",
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
                name=UK_CHAT_DATASET.name,
                label="Enhanced FRS 2024-25",
                uri="hf://example",
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
        name=UK_CHAT_DATASET.name,
        label="Enhanced FRS 2024-25",
        uri="hf://example",
        row_level_access=False,
    )
    monkeypatch.setattr(simulation_engine, "resolve_dataset", lambda: dataset)

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


def test_society_simulation_uses_fixed_dataset(monkeypatch):
    captured = {}
    dataset = DatasetSpec(
        name=UK_CHAT_DATASET.name,
        label=UK_CHAT_DATASET.label,
        uri=UK_CHAT_DATASET.uri,
        row_level_access=False,
    )
    monkeypatch.setattr(simulation_engine, "resolve_dataset", lambda: dataset)

    def fake_pair(**kwargs):
        captured.update(kwargs)
        baseline = object()
        return baseline, baseline

    monkeypatch.setattr(simulation_engine, "managed_simulation_pair", fake_pair)

    result = simulation_engine.build_society_simulation(
        year=2026,
        reform=None,
    )

    assert "dataset" not in captured
    assert result.dataset == dataset


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


def test_decile_chart_preserves_missing_values_and_concept_labels():
    context = new_tool_context("missing-wealth-decile")
    result_id = context.result_store.put(
        "decile_impacts",
        object(),
        {
            "decile_concept": "wealth",
            "measure_label": "household net income",
            "grouping_label": "Wealth decile",
            "deciles": [
                {"decile": 1, "absolute_change": None, "relative_change": None},
                {"decile": 2, "absolute_change": 25, "relative_change": 1.5},
            ],
        },
    )

    chart = agent_tools.generate_chart(
        chart_kind="decile_absolute_bar",
        result_id=result_id,
        _context=context,
    )

    assert chart["spec"]["measureLabel"] == "household net income"
    assert chart["spec"]["groupLabel"] == "Wealth decile"
    assert chart["spec"]["data"] == [
        {"label": "1", "value": None},
        {"label": "2", "value": 25},
    ]


def test_winners_losers_chart_uses_stored_grouping_label():
    context = new_tool_context("wealth-winners-losers")
    result_id = context.result_store.put(
        "winners_losers",
        object(),
        {
            "basis": "wealth",
            "grouping_label": "Wealth decile",
            "deciles": [{"decile": 1, "no_change": None}],
        },
    )

    chart = agent_tools.generate_chart(
        chart_kind="winners_losers_stacked_bar",
        result_id=result_id,
        _context=context,
    )

    assert chart["spec"]["groupLabel"] == "Wealth decile"
    assert chart["spec"]["data"][0]["no_change"] is None


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
