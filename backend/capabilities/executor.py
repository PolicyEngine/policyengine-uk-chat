"""Authorized validation, dispatch, cancellation, and trace recording."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ValidationError

from capabilities.context import (
    ArtifactAccess,
    CancellationProbe,
    CapabilityContext,
    EmptyArtifactAccess,
    ModelUsageLedger,
)
from capabilities.contracts import (
    Accepted,
    Capability,
    CapabilityOutcome,
    Completed,
    Failed,
    NeedsInput,
    Unsupported,
)
from capabilities.registry import CapabilityRegistry
from capabilities.tracing import (
    debug_projection,
    InvocationKind,
    InvocationStatus,
    InvocationTracer,
)
from tools.contracts import CallerType
from tools.context import TurnResultStore
from tools.registry import ToolRegistry

if TYPE_CHECKING:
    from conversation_context.models import ConversationContext


_PARENT_INVOCATION: ContextVar[str | None] = ContextVar(
    "capability_parent_invocation",
    default=None,
)


class InvocationCancelled(Exception):
    pass


class InvocationTraceValues(Protocol):
    """Select the validated values represented by one invocation trace."""

    def input_value(
        self,
        *,
        raw_input: object,
        validated_input: BaseModel,
    ) -> object: ...

    def output_value(self, validated_output: object) -> object: ...

    def failed_output(self) -> object: ...

    def cancelled_output(self) -> object: ...


class RegisteredBoundaryTraceValues:
    """Represent the typed input and output seen by a registered operation."""

    def input_value(
        self,
        *,
        raw_input: object,
        validated_input: BaseModel,
    ) -> object:
        del raw_input
        return validated_input

    def output_value(self, validated_output: object) -> object:
        return validated_output

    def failed_output(self) -> object:
        return {"status": "failed"}

    def cancelled_output(self) -> object:
        return {"status": "cancelled"}


_REGISTERED_BOUNDARY_TRACE_VALUES = RegisteredBoundaryTraceValues()


class InvocationExecutor:
    """Execute only registered, authorized operations with typed boundaries."""

    def __init__(
        self,
        *,
        tools: ToolRegistry,
        capabilities: CapabilityRegistry,
        tracer: InvocationTracer,
    ) -> None:
        self._tools = tools
        self._capabilities = capabilities
        self._tracer = tracer

    @property
    def tracer(self) -> InvocationTracer:
        """Expose only sanitized trace projections to the chat adapter."""

        return self._tracer

    def context(
        self,
        *,
        request_id: str,
        conversation_id: str,
        turn_id: str,
        is_cancelled: CancellationProbe,
        artifacts: ArtifactAccess | None = None,
        result_store: TurnResultStore | None = None,
        model_usage: ModelUsageLedger | None = None,
        conversation_context: "ConversationContext | None" = None,
    ) -> CapabilityContext:
        return CapabilityContext(
            request_id=request_id,
            conversation_id=conversation_id,
            turn_id=turn_id,
            is_cancelled=is_cancelled,
            artifacts=artifacts or EmptyArtifactAccess(),
            result_store=result_store or TurnResultStore(),
            model_usage=model_usage or ModelUsageLedger(),
            conversation_context=conversation_context,
            _executor=self,
        )

    async def invoke_tool(
        self,
        identifier: str,
        raw_input: object,
        *,
        caller: CallerType,
        context: CapabilityContext,
    ) -> BaseModel:
        tool = self._tools.get(identifier, caller=caller)
        self._validate_declared_tool_call(identifier, caller, context)
        tool_input = self._validate(tool.spec.input_model, raw_input, identifier, "input")
        record = self._tracer.start(
            conversation_id=context.conversation_id,
            turn_id=context.turn_id,
            parent_invocation_id=_PARENT_INVOCATION.get(),
            kind=InvocationKind.TOOL,
            identifier=identifier,
            version=tool.spec.version,
            visibility=tool.spec.visibility,
            summary=self._trace_summary("tool", identifier, "started"),
            debug_input=debug_projection(tool_input),
        )
        token = _PARENT_INVOCATION.set(record.invocation_id)
        try:
            await self._check_cancelled(context)
            raw_output = await tool.run(tool_input, context.for_tool(identifier))
            output = self._validate(
                tool.spec.output_model,
                raw_output,
                identifier,
                "output",
            )
            await self._check_cancelled(context)
        except (InvocationCancelled, asyncio.CancelledError):
            self._tracer.finish(
                record.invocation_id,
                status=InvocationStatus.CANCELLED,
                summary=self._trace_summary("tool", identifier, "cancelled"),
                debug_output={"status": "cancelled"},
            )
            raise
        except Exception:
            self._tracer.finish(
                record.invocation_id,
                status=InvocationStatus.FAILED,
                summary=self._trace_summary("tool", identifier, "failed"),
                debug_output={"status": "failed"},
            )
            raise
        else:
            self._tracer.finish(
                record.invocation_id,
                status=InvocationStatus.COMPLETED,
                summary=self._trace_summary("tool", identifier, "completed"),
                debug_output=debug_projection(output),
            )
            return output
        finally:
            _PARENT_INVOCATION.reset(token)

    async def invoke_capability(
        self,
        identifier: str,
        raw_input: object,
        *,
        caller: CallerType,
        context: CapabilityContext,
        trace_values: InvocationTraceValues = _REGISTERED_BOUNDARY_TRACE_VALUES,
    ) -> CapabilityOutcome[BaseModel]:
        capability = self._capabilities.get(identifier, caller=caller)
        self._validate_declared_capability_call(identifier, caller, context)
        capability_input = self._validate(
            capability.spec.input_model,
            raw_input,
            identifier,
            "input",
        )
        record = self._tracer.start(
            conversation_id=context.conversation_id,
            turn_id=context.turn_id,
            parent_invocation_id=_PARENT_INVOCATION.get(),
            kind=InvocationKind.CAPABILITY,
            identifier=identifier,
            version=capability.spec.version,
            visibility=capability.spec.visibility,
            summary=self._trace_summary("capability", identifier, "started"),
            debug_input=debug_projection(
                trace_values.input_value(
                    raw_input=raw_input,
                    validated_input=capability_input,
                )
            ),
        )
        token = _PARENT_INVOCATION.set(record.invocation_id)
        try:
            await self._check_cancelled(context)
            outcome = await capability.run(
                capability_input,
                context.for_capability(
                    identifier,
                    record.invocation_id,
                    capability.spec.version,
                ),
            )
            validated = self._validate_outcome(capability, outcome)
            await self._check_cancelled(context)
            trace_status = self._outcome_status(validated)
        except (InvocationCancelled, asyncio.CancelledError):
            self._tracer.finish(
                record.invocation_id,
                status=InvocationStatus.CANCELLED,
                summary=self._trace_summary("capability", identifier, "cancelled"),
                debug_output=debug_projection(trace_values.cancelled_output()),
            )
            raise
        except Exception:
            self._tracer.finish(
                record.invocation_id,
                status=InvocationStatus.FAILED,
                summary=self._trace_summary("capability", identifier, "failed"),
                debug_output=debug_projection(trace_values.failed_output()),
            )
            raise
        else:
            self._tracer.finish(
                record.invocation_id,
                status=trace_status,
                summary=self._trace_summary(
                    "capability",
                    identifier,
                    validated.status,
                ),
                debug_output=debug_projection(
                    trace_values.output_value(validated)
                ),
            )
            return validated
        finally:
            _PARENT_INVOCATION.reset(token)

    @staticmethod
    def _validate(
        model: type[BaseModel],
        value: object,
        identifier: str,
        boundary: str,
    ) -> BaseModel:
        try:
            if isinstance(value, model):
                return value
            return model.model_validate(value)
        except ValidationError as exc:
            raise TypeError(
                f"Invalid {boundary} for registered operation {identifier}."
            ) from exc

    @staticmethod
    def _validate_outcome(
        capability: Capability[Any, Any],
        outcome: object,
    ) -> CapabilityOutcome[BaseModel]:
        if isinstance(outcome, Completed):
            value = InvocationExecutor._validate(
                capability.spec.output_model,
                outcome.value,
                capability.spec.identifier,
                "completed output",
            )
            return Completed(value=value)
        if isinstance(outcome, NeedsInput):
            unknown = set(outcome.partial_input) - set(
                capability.spec.input_model.model_fields
            )
            if unknown:
                raise TypeError(
                    f"Capability {capability.spec.identifier} returned unknown partial "
                    f"input fields: {sorted(unknown)}."
                )
            return outcome
        if isinstance(outcome, (Unsupported, Failed)):
            return outcome
        if isinstance(outcome, Accepted):
            raise TypeError(
                "Accepted is reserved for a future explicitly asynchronous capability."
            )
        raise TypeError(
            f"Capability {capability.spec.identifier} returned an invalid outcome."
        )

    @staticmethod
    def _outcome_status(outcome: CapabilityOutcome[BaseModel]) -> InvocationStatus:
        if isinstance(outcome, Completed):
            return InvocationStatus.COMPLETED
        if isinstance(outcome, NeedsInput):
            return InvocationStatus.NEEDS_INPUT
        if isinstance(outcome, Unsupported):
            return InvocationStatus.UNSUPPORTED
        return InvocationStatus.FAILED

    async def _check_cancelled(self, context: CapabilityContext) -> None:
        if await context.cancelled():
            raise InvocationCancelled

    @staticmethod
    def _trace_summary(kind: str, identifier: str, status: str) -> str:
        """Build trace text exclusively from registered metadata and status."""

        return f"{kind} {identifier} {status}"

    def _validate_declared_tool_call(
        self,
        identifier: str,
        caller: CallerType,
        context: CapabilityContext,
    ) -> None:
        if caller is CallerType.TOOL:
            source_id = context._tool_id
            if source_id is None:
                raise PermissionError("Tool caller identity is required.")
            tool_source = self._tools.registered(source_id)
            if identifier not in tool_source.spec.tool_dependencies:
                raise PermissionError(
                    f"Tool {source_id} did not declare tool dependency {identifier}."
                )
            return
        if caller is not CallerType.CAPABILITY:
            return
        source_id = context._capability_id
        if source_id is None:
            raise PermissionError("Capability caller identity is required.")
        capability_source = self._capabilities.registered(source_id)
        if identifier not in capability_source.spec.tool_dependencies:
            raise PermissionError(
                f"Capability {source_id} did not declare tool dependency {identifier}."
            )

    def _validate_declared_capability_call(
        self,
        identifier: str,
        caller: CallerType,
        context: CapabilityContext,
    ) -> None:
        if caller is not CallerType.CAPABILITY:
            return
        source_id = context._capability_id
        if source_id is None:
            raise PermissionError("Capability caller identity is required.")
        source = self._capabilities.registered(source_id)
        declared = {
            dependency.capability_id for dependency in source.spec.dependencies
        }
        if identifier not in declared:
            raise PermissionError(
                f"Capability {source_id} did not declare capability dependency {identifier}."
            )
