"""Unit and architecture tests for typed capability composition."""

from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from capabilities.composition import compose_runtime
from capabilities.application import build_capability_chat_application
from capabilities.contracts import (
    ArtifactContract,
    Capability,
    CapabilityDependency,
    CapabilitySpec,
    Completed,
    NeedsInput,
)
from capabilities.executor import InvocationCancelled
from capabilities.registry import CapabilityRegistry
from capabilities.tracing import InvocationKind, InvocationStatus
from tools.contracts import CallerType, Tool, ToolSpec, Visibility


def test_concrete_application_composes_every_registered_operation(tmp_path):
    from sqlmodel import SQLModel, create_engine

    engine = create_engine(f"sqlite:///{tmp_path / 'application.sqlite'}")
    SQLModel.metadata.create_all(engine)

    application = build_capability_chat_application(engine=engine)

    assert {spec.identifier for spec in application.composition.capabilities.specs()} == {
        "conversation_relevance",
        "policy_information",
        "policy_reform",
        "household_analysis",
        "society_analysis",
        "analysis_follow_up",
        "society_chart",
    }
    assert len(application.composition.tools.specs()) == 32
    operation_ids = {
        spec.identifier for spec in application.composition.tools.specs()
    }
    assert {
        "propose_context_change",
        "validate_context_change",
        "resolve_context_change",
        "apply_context_change",
    } <= operation_ids
from tools.registry import ToolRegistry


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NumberInput(StrictModel):
    value: int


class NumberOutput(StrictModel):
    value: int


class AddOneTool(Tool[NumberInput, NumberOutput]):
    spec = ToolSpec(
        identifier="add_one",
        version="1",
        description="Add one to a number.",
        visibility=Visibility.PRIVATE,
        allowed_callers=frozenset({CallerType.CAPABILITY}),
        input_model=NumberInput,
        output_model=NumberOutput,
    )

    async def run(self, tool_input: NumberInput, context) -> NumberOutput:
        del context
        return NumberOutput(value=tool_input.value + 1)


class PublicRestrictedTool(AddOneTool):
    spec = AddOneTool.spec.model_copy(
        update={
            "identifier": "public_restricted",
            "visibility": Visibility.PUBLIC,
        }
    )


class BrokenOutputTool(AddOneTool):
    spec = AddOneTool.spec.model_copy(update={"identifier": "broken"})

    async def run(self, tool_input: NumberInput, context) -> NumberOutput:
        del tool_input, context
        return {"wrong": 1}  # type: ignore[return-value]


class RuntimeBrokenOutputTool(BrokenOutputTool):
    spec = BrokenOutputTool.spec.model_copy(
        update={
            "identifier": "runtime_broken",
            "allowed_callers": frozenset({CallerType.RUNTIME}),
        }
    )


class AddOneCapability(Capability[NumberInput, NumberOutput]):
    spec = CapabilitySpec(
        identifier="add_one_capability",
        version="1",
        description="Return the input plus one.",
        required_use="Use only in this composition test.",
        visibility=Visibility.PUBLIC,
        allowed_callers=frozenset({CallerType.RUNTIME}),
        input_model=NumberInput,
        output_model=NumberOutput,
        tool_dependencies=("add_one",),
    )

    async def run(self, capability_input: NumberInput, context):
        output = await context.invoke_tool("add_one", capability_input)
        return Completed(value=output)


class ClarificationCapability(AddOneCapability):
    spec = AddOneCapability.spec.model_copy(
        update={"identifier": "clarification", "tool_dependencies": ()}
    )

    async def run(self, capability_input: NumberInput, context):
        del capability_input, context
        return NeedsInput(
            prompt="What value should I use?",
            missing_fields=("value",),
            partial_input={},
        )


async def not_cancelled() -> bool:
    return False


def _context(composition, cancellation=not_cancelled):
    return composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=cancellation,
    )


def test_tool_and_capability_specs_require_explicit_visibility_and_are_immutable():
    with pytest.raises(ValidationError):
        ToolSpec(
            identifier="missing_visibility",
            version="1",
            description="Invalid.",
            allowed_callers=frozenset({CallerType.RUNTIME}),
            input_model=NumberInput,
            output_model=NumberOutput,
        )

    with pytest.raises(ValidationError):
        CapabilitySpec(
            identifier="missing_visibility",
            version="1",
            description="Invalid.",
            required_use="Never.",
            allowed_callers=frozenset({CallerType.RUNTIME}),
            input_model=NumberInput,
            output_model=NumberOutput,
        )

    with pytest.raises(ValidationError):
        AddOneTool.spec.identifier = "changed"  # type: ignore[misc]


def test_tool_registry_rejects_duplicates_and_filters_by_caller_and_visibility():
    registry = ToolRegistry()
    registry.register(AddOneTool())
    registry.register(PublicRestrictedTool())

    assert registry.definitions_for(CallerType.MODEL) == []
    assert registry.definitions_for(CallerType.CAPABILITY) == [
        {
            "name": "public_restricted",
            "description": "Add one to a number.",
            "input_schema": NumberInput.model_json_schema(),
        }
    ]
    assert [
        definition["name"]
        for definition in registry.definitions_for(
            CallerType.CAPABILITY,
            include_private=True,
        )
    ] == ["add_one", "public_restricted"]
    with pytest.raises(ValueError, match="Duplicate typed tool"):
        registry.register(AddOneTool())
    with pytest.raises(PermissionError):
        registry.get("public_restricted", caller=CallerType.MODEL)


def _dependency_capability(
    identifier: str,
    *,
    dependencies=(),
    accepted_artifacts=(),
    produced_artifacts=(),
):
    class DependencyCapability(AddOneCapability):
        spec = AddOneCapability.spec.model_copy(
            update={
                "identifier": identifier,
                "tool_dependencies": (),
                "dependencies": dependencies,
                "accepted_artifacts": accepted_artifacts,
                "produced_artifacts": produced_artifacts,
            }
        )

        async def run(self, capability_input, context):
            del context
            return Completed(value=NumberOutput(value=capability_input.value))

    return DependencyCapability()


def test_capability_registry_rejects_unknown_dependencies_cycles_and_artifact_mismatch():
    unknown = CapabilityRegistry()
    unknown.register(
        _dependency_capability(
            "consumer",
            dependencies=(CapabilityDependency(capability_id="missing"),),
        )
    )
    with pytest.raises(ValueError, match="unknown capability missing"):
        unknown.validate()

    cyclic = CapabilityRegistry()
    cyclic.register(
        _dependency_capability(
            "first",
            dependencies=(CapabilityDependency(capability_id="second"),),
        )
    )
    cyclic.register(
        _dependency_capability(
            "second",
            dependencies=(CapabilityDependency(capability_id="first"),),
        )
    )
    with pytest.raises(ValueError, match="first -> second -> first"):
        cyclic.validate()

    scenario_v1 = ArtifactContract(
        artifact_type="policy_scenario",
        schema_version="1",
    )
    scenario_v2 = scenario_v1.model_copy(update={"schema_version": "2"})
    incompatible = CapabilityRegistry()
    incompatible.register(
        _dependency_capability(
            "provider",
            produced_artifacts=(scenario_v1,),
        )
    )
    incompatible.register(
        _dependency_capability(
            "consumer",
            dependencies=(
                CapabilityDependency(
                    capability_id="provider",
                    artifact=scenario_v2,
                ),
            ),
            accepted_artifacts=(scenario_v2,),
        )
    )
    with pytest.raises(ValueError, match="incompatible artifact"):
        incompatible.validate()


def test_executor_validates_nested_calls_and_records_parent_aware_trace():
    composition = compose_runtime(
        tools=[AddOneTool()],
        capabilities=[AddOneCapability()],
    )

    outcome = asyncio.run(
        composition.executor.invoke_capability(
            "add_one_capability",
            {"value": 4},
            caller=CallerType.RUNTIME,
            context=_context(composition),
        )
    )

    assert isinstance(outcome, Completed)
    assert outcome.value == NumberOutput(value=5)
    records = composition.tracer.records(
        "conversation-1",
        include_private=True,
    )
    assert [record.kind for record in records] == [
        InvocationKind.CAPABILITY,
        InvocationKind.TOOL,
    ]
    assert records[1].parent_invocation_id == records[0].invocation_id
    assert [record.status for record in records] == [
        InvocationStatus.COMPLETED,
        InvocationStatus.COMPLETED,
    ]
    assert composition.tracer.records(
        "conversation-1",
        include_private=False,
    ) == (records[0],)


def test_executor_rejects_invalid_input_output_and_undeclared_nested_calls():
    composition = compose_runtime(
        tools=[BrokenOutputTool(), RuntimeBrokenOutputTool()],
        capabilities=[ClarificationCapability()],
    )
    context = _context(composition)

    with pytest.raises(TypeError, match="Invalid input"):
        asyncio.run(
            composition.executor.invoke_tool(
                "runtime_broken",
                {"value": 1, "extra": True},
                caller=CallerType.RUNTIME,
                context=context,
            )
        )

    with pytest.raises(PermissionError, match="did not declare tool dependency"):
        asyncio.run(
            composition.executor.invoke_tool(
                "broken",
                {"value": 1},
                caller=CallerType.CAPABILITY,
                context=context.for_capability("clarification"),
            )
        )

    with pytest.raises(TypeError, match="Invalid output"):
        asyncio.run(
            composition.executor.invoke_tool(
                "runtime_broken",
                {"value": 1},
                caller=CallerType.RUNTIME,
                context=context,
            )
        )


def test_needs_input_is_typed_and_cancellation_is_checked_before_dispatch():
    composition = compose_runtime(
        tools=[],
        capabilities=[ClarificationCapability()],
    )
    outcome = asyncio.run(
        composition.executor.invoke_capability(
            "clarification",
            {"value": 1},
            caller=CallerType.RUNTIME,
            context=_context(composition),
        )
    )
    assert isinstance(outcome, NeedsInput)
    assert outcome.missing_fields == ("value",)

    async def cancelled() -> bool:
        return True

    with pytest.raises(InvocationCancelled):
        asyncio.run(
            composition.executor.invoke_capability(
                "clarification",
                {"value": 1},
                caller=CallerType.RUNTIME,
                context=_context(composition, cancelled),
            )
        )


def test_startup_composition_rejects_unknown_tool_dependencies():
    with pytest.raises(ValueError, match="requires unknown tools"):
        compose_runtime(tools=[], capabilities=[AddOneCapability()])


def test_executor_exposes_no_selection_or_input_resolution_operations():
    composition = compose_runtime(tools=[], capabilities=[])
    executor = composition.executor

    assert not hasattr(executor, "select_capability")
    assert not hasattr(executor, "infer_intent")
    assert not hasattr(executor, "resolve_input")
