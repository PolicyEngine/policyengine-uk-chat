from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from capabilities.composition import compose_runtime
from capabilities.contracts import Capability, CapabilitySpec, Completed
from tools.analysis_support import (
    ExtractResultFindingsTool,
    NumericalFact,
    SelectSupportedOutputsTool,
    VerifyNumericalResponseTool,
)
from chat.narration import NumericalNarrationVerifier
from tools.contracts import CallerType, Visibility
from tools.typed_dispatch import build_dispatch_tools
from tools.typed_models import SafeToolOutput


VALID_RETAINED_OUTPUTS = {
    "list_entities": {"status": "success", "entities": []},
    "search_variables": {"status": "success", "variables": []},
    "get_variable": {"status": "success", "variable": {"name": "age"}},
    "search_parameters": {"status": "success", "parameters": []},
    "get_parameter": {"status": "success", "parameter": {"path": "gov.test"}},
    "list_reform_targets": {"status": "success", "targets": []},
    "list_household_input_variables": {
        "status": "success",
        "query": "",
        "entity": None,
        "variables": [],
        "input_contract": "typed household input",
    },
    "list_society_output_variables": {
        "status": "success",
        "entity": None,
        "default_variables_by_entity": {},
        "default_variable_count": 0,
        "extra_variables_contract": "typed extra variables",
    },
    "list_supported_outputs": {"status": "success", "scope": None, "outputs": []},
    "validate_reform": {"valid": True},
    "validate_household": {"valid": True},
    "run_household_simulation": {
        "status": "success",
        "year": 2026,
        "reform_applied": False,
        "result_id": "household-result",
    },
    "run_society_simulation": {
        "status": "success",
        "year": 2026,
        "result_id": "society-result",
    },
    "compute_budgetary_impact": {
        "status": "success",
        "simulation_id": "simulation",
        "net_cost": 1.0,
        "result_id": "budget-result",
    },
    "compute_program_breakdown": {
        "status": "success",
        "simulation_id": "simulation",
        "programs": [],
        "result_id": "program-result",
    },
    "compute_decile_impacts": {
        "status": "success",
        "simulation_id": "simulation",
        "deciles": [],
        "result_id": "decile-result",
    },
    "compute_winners_losers": {
        "status": "success",
        "simulation_id": "simulation",
        "winners": 1.0,
        "losers": 1.0,
        "unchanged": 1.0,
        "result_id": "incidence-result",
    },
    "compute_poverty_metrics": {
        "status": "success",
        "simulation_id": "simulation",
        "overall_rate": 0.2,
        "change": 0.01,
        "result_id": "poverty-result",
    },
    "compute_inequality_metrics": {
        "status": "success",
        "simulation_id": "simulation",
        "gini": 0.3,
        "result_id": "inequality-result",
    },
    "aggregate_result": {
        "status": "success",
        "simulation_id": "simulation",
        "result": {},
        "privacy": "aggregate-only",
        "result_id": "aggregate-result",
    },
    "generate_chart": {
        "status": "success",
        "chart_markdown": "```chart\n{}\n```",
        "spec": {},
    },
}


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyCapabilityInput(StrictModel):
    pass


class EmptyCapabilityOutput(StrictModel):
    pass


class ToolCallerCapability(Capability[EmptyCapabilityInput, EmptyCapabilityOutput]):
    spec = CapabilitySpec(
        identifier="tool_caller",
        version="1",
        description="Test declared typed tool calls.",
        required_use="Test only.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=EmptyCapabilityInput,
        output_model=EmptyCapabilityOutput,
        tool_dependencies=(
            "run_society_simulation",
            "validate_reform",
            "select_supported_outputs",
            "extract_result_findings",
        ),
    )

    async def run(self, capability_input, context):
        del capability_input, context
        return Completed(value=EmptyCapabilityOutput())


async def not_cancelled() -> bool:
    return False


def _composition(*extra_tools):
    tools = [
        *build_dispatch_tools(),
        SelectSupportedOutputsTool(),
        ExtractResultFindingsTool(),
        *extra_tools,
    ]
    tools_by_id = {tool.spec.identifier: tool for tool in tools}
    return compose_runtime(
        tools=tools_by_id.values(),
        capabilities=[ToolCallerCapability()],
    )


def _context(composition):
    return composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
    )


def test_retained_inventory_has_strict_typed_contracts_and_explicit_visibility():
    tools = build_dispatch_tools()

    assert len(tools) == 21
    assert {tool.spec.visibility for tool in tools} == {
        Visibility.PUBLIC,
        Visibility.PRIVATE,
    }
    assert {
        tool.spec.identifier
        for tool in tools
        if tool.spec.visibility is Visibility.PRIVATE
    } == {"validate_reform", "validate_household"}
    for tool in tools:
        schema = tool.spec.input_model.model_json_schema()
        assert schema["additionalProperties"] is False
        assert issubclass(tool.spec.output_model, BaseModel)
    assert len({tool.spec.output_model for tool in tools}) == len(tools)


def test_retained_output_models_reject_another_operation_result_shape():
    tools = {tool.spec.identifier: tool for tool in build_dispatch_tools()}

    with pytest.raises(ValidationError, match="missing required fields"):
        tools["list_entities"].spec.output_model.model_validate(
            {
                "status": "success",
                "query": "income",
                "variables": [],
            }
        )


def test_every_retained_output_contract_accepts_its_shape_and_rejects_another():
    tools = {tool.spec.identifier: tool for tool in build_dispatch_tools()}

    assert set(tools) == set(VALID_RETAINED_OUTPUTS)
    for identifier, tool in tools.items():
        tool.spec.output_model.model_validate(VALID_RETAINED_OUTPUTS[identifier])
        wrong_shapes_rejected = 0
        for other_identifier, payload in VALID_RETAINED_OUTPUTS.items():
            if other_identifier == identifier:
                continue
            try:
                tool.spec.output_model.model_validate(payload)
            except ValidationError:
                wrong_shapes_rejected += 1
        assert wrong_shapes_rejected > 0, identifier

    with pytest.raises(ValidationError, match="unexpected fields"):
        tools["run_society_simulation"].spec.output_model.model_validate(
            {
                "status": "success",
                "year": 2026,
                "result_id": "result-1",
                "programs": [],
            }
        )


def test_dispatch_adapter_validates_input_and_keeps_provider_payload_request_local(
    monkeypatch,
):
    from tools import typed_dispatch

    provider_payload = object()
    received = {}

    def fake_execute(identifier, payload, context=None):
        received.update(identifier=identifier, payload=payload, context=context)
        result_id = context.result_store.put(
            "society_simulation",
            provider_payload,
            {"status": "success", "year": 2026},
        )
        return {"status": "success", "year": 2026, "result_id": result_id}

    monkeypatch.setattr(typed_dispatch, "execute_tool", fake_execute)
    composition = _composition()
    context = _context(composition)

    output = asyncio.run(
        composition.executor.invoke_tool(
            "run_society_simulation",
            {"year": 2026},
            caller=CallerType.CAPABILITY,
            context=context.for_capability("tool_caller"),
        )
    )

    assert isinstance(output, SafeToolOutput)
    result_id = output.root["result_id"]
    assert isinstance(result_id, str)
    assert context.result_store.get(result_id, "society_simulation").payload is (
        provider_payload
    )
    assert "payload" not in output.root
    assert received["identifier"] == "run_society_simulation"
    with pytest.raises(TypeError, match="Invalid input"):
        asyncio.run(
            composition.executor.invoke_tool(
                "run_society_simulation",
                {"year": 2026, "dataset": "model-authored"},
                caller=CallerType.CAPABILITY,
                context=context.for_capability("tool_caller"),
            )
        )
    trace_text = " ".join(
        record.summary
        for record in composition.tracer.records(
            "conversation-1",
            include_private=True,
        )
    )
    assert result_id not in trace_text


def test_private_validation_tools_are_not_model_callable():
    composition = _composition()

    with pytest.raises(PermissionError):
        asyncio.run(
            composition.executor.invoke_tool(
                "validate_reform",
                {"reform": {"gov.example": 1}, "year": 2026},
                caller=CallerType.MODEL,
                context=_context(composition),
            )
        )


def test_failed_validation_creates_no_result_and_does_not_invoke_simulation(monkeypatch):
    from tools import typed_dispatch

    calls = []

    def fake_execute(identifier, payload, context=None):
        del payload, context
        calls.append(identifier)
        if identifier == "validate_reform":
            return {"status": "error", "error": "Unknown parameter"}
        raise AssertionError("A failed validation must not invoke another tool")

    monkeypatch.setattr(typed_dispatch, "execute_tool", fake_execute)
    composition = _composition()
    context = _context(composition)
    output = asyncio.run(
        composition.executor.invoke_tool(
            "validate_reform",
            {"reform": {"invented.path": 1}, "year": 2026},
            caller=CallerType.CAPABILITY,
            context=context.for_capability("tool_caller"),
        )
    )

    assert output.root["status"] == "error"
    assert calls == ["validate_reform"]
    assert context.result_store._items == {}


def test_safe_output_rejects_row_level_provider_data():
    with pytest.raises(ValidationError, match="row-level data"):
        SafeToolOutput.model_validate(
            {
                "status": "success",
                "nested": {"survey_records": [{"person": 1}]},
            }
        )


def test_supported_output_selection_uses_registry_defaults_and_typed_issues():
    composition = _composition(SelectSupportedOutputsTool())
    context = _context(composition).for_capability("tool_caller")

    output = asyncio.run(
        composition.executor.invoke_tool(
            "select_supported_outputs",
            {
                "requested_outputs": [
                    "poverty rate",
                    "budget cost",
                    "Canadian GDP",
                ]
            },
            caller=CallerType.CAPABILITY,
            context=context,
        )
    )

    assert output.output_ids == (
        "budgetary_impact",
        "winners_losers",
        "decile_impacts",
        "poverty",
    )
    assert output.issues[0].request == "Canadian GDP"
    assert output.issues[0].kind == "unsupported"


def test_findings_project_only_validated_aggregate_values():
    from capabilities.artifacts import AggregateValue

    composition = _composition(ExtractResultFindingsTool())
    output = asyncio.run(
        composition.executor.invoke_tool(
            "extract_result_findings",
            {
                "outputs": [
                    {
                        "output_id": "budgetary_impact",
                        "metric_id": "net_cost",
                        "label": "Net budget cost",
                        "value": 1_000_000,
                        "unit": "GBP/year",
                    }
                ]
            },
            caller=CallerType.CAPABILITY,
            context=_context(composition).for_capability("tool_caller"),
        )
    )

    assert output.findings[0].model_dump() == AggregateValue(
        output_id="budgetary_impact",
        metric_id="net_cost",
        label="Net budget cost",
        value=1_000_000,
        unit="GBP/year",
    ).model_dump()


@pytest.mark.parametrize(
    ("draft", "supported"),
    [
        ("The reform costs £1.2 billion.", True),
        ("1. The reform costs £1.2 billion.\n2. Ask a follow-up.", True),
        ("The rate rises by 2.5%.", True),
        ("The reform saves £1.2 billion.", False),
        ("The reform costs £2 billion.", False),
    ],
)
def test_numerical_verifier_checks_sign_scale_percent_and_rounding(draft, supported):
    composition = compose_runtime(
        tools=[VerifyNumericalResponseTool()],
        capabilities=[],
    )
    facts = [NumericalFact(label="Net cost", value=1_200_000_000, unit="GBP/year")]
    if "%" in draft:
        facts = [NumericalFact(label="Rate change", value=0.025, unit="percentage")]
    if "saves" in draft:
        facts = [NumericalFact(label="Net cost", value=-1_200_000_000, unit="GBP/year")]

    output = asyncio.run(
        composition.executor.invoke_tool(
            "verify_numerical_response",
            {"draft": draft, "facts": [fact.model_dump() for fact in facts]},
            caller=CallerType.RUNTIME,
            context=_context(composition),
        )
    )

    assert output.supported is supported
    assert bool(output.unsupported_claims) is not supported
    assert "Net cost" in output.deterministic_fact_summary or "%" in draft


def test_narration_verifier_allows_one_correction_then_uses_fact_fallback():
    composition = compose_runtime(
        tools=[VerifyNumericalResponseTool()],
        capabilities=[],
    )
    verifier = NumericalNarrationVerifier(composition.executor)
    redrafts = []

    async def still_wrong(draft, result):
        redrafts.append((draft, result))
        return "The cost is £3 billion."

    result = asyncio.run(
        verifier.finalize(
            draft="The cost is £2 billion.",
            facts=(
                NumericalFact(
                    label="Net cost",
                    value=1_200_000_000,
                    unit="GBP/year",
                ),
            ),
            context=_context(composition),
            redraft=still_wrong,
        )
    )

    assert len(redrafts) == 1
    assert result == "Net cost: 1.2e+09 GBP/year"
