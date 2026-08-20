"""Application service for one stateful policy-analysis turn."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import secrets
import threading
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import partial
from importlib.metadata import PackageNotFoundError, version
from typing import Literal, TypeAlias

from analysis.clarifications import render_clarification
from analysis.common import AnalysisError, AnalysisErrorCode, RuntimeVersions, stable_identifier
from analysis.commands import build_plan_claim_command
from analysis.dependencies import (
    AsyncCancellationProbe,
    BillingIntentBuilder,
    ExecutionService,
    NarrationService,
    RequestCompilationService,
    RuntimeVersionProvider,
    TurnInterpreter,
)
from analysis.execution_engine import (
    CallbackExecutionControl,
    ExecutionEngine,
    ExecutionProgress,
    ExecutionRequest,
    ExecutionResult,
    failed_execution_result,
)
from analysis.executor import EXPLORATORY_MODEL
from analysis.finalization import (
    FinalizationResult,
    finalize_turn,
    outcome_is_billable,
    replay_outcome,
)
from analysis.interpreter import (
    INTERPRETER_MODEL,
    InterpretationFailure,
    InterpretationResult,
    InterpretationUsage,
    InterpreterContext,
    interpret_turn,
)
from analysis.lifecycle import (
    AttemptOutcomeEvent,
    CancellationRequestedEvent,
    ClarificationRequiredEvent,
    ConflictObservedEvent,
    ConversationAdvancedEvent,
    ExplanationOutcomeEvent,
    LifecycleReducer,
    PlanReadyEvent,
    RecoveryEvent,
    RequestRejectedEvent,
    TurnFailedEvent,
)
from analysis.models import (
    AnalysisSessionState,
    CancelledTurnOutcome,
    ClarificationTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    ExecutionCompletion,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    ExecutionMode,
    FactRegister,
    FailedTurnOutcome,
    ModelUsageEntry,
    ExecutionPlan,
    SemanticRequestRevision,
    SessionId,
    TurnId,
    TurnOutcome,
    TurnReceipt,
    UnsupportedTurnOutcome,
    ValidatedAnswerClarification,
    ValidatedAskAboutExecution,
    ValidatedCancelAnalysis,
    WorkflowTransition,
)
from analysis.narration import (
    NARRATION_MODEL,
    NarrationFailure,
    NarrationResult,
    answer_execution_question,
    narrate_execution_result,
)
from analysis.store import (
    AnalysisStore,
    BeginTurnCommand,
    DEFAULT_EXECUTION_LEASE_SECONDS,
    DEFAULT_EXECUTION_HEARTBEAT_SECONDS,
    DEFAULT_PROCESSING_RECEIPT_TIMEOUT_SECONDS,
    HeartbeatAttemptCommand,
    LoadOrCreateSessionCommand,
)
from analysis.request_compiler import (
    CompilationClarification,
    CompilationInput,
    CompiledRequest,
    RequestCompilationFailed,
    RequestCompilation,
    RequestCompiler,
    RequestUnsupported,
)
from analysis.trace import AnalysisTrace


logger = logging.getLogger(__name__)
REPLACEMENT_WAIT_TIMEOUT_SECONDS = 600
CONFLICT_RESPONSE_CONTENT = (
    "This request could not be applied because the conversation changed while "
    "it was processing. Retry it using the latest conversation state."
)


@dataclass(frozen=True, slots=True)
class TextTurnBlock:
    text: str
    type: Literal["text"] = field(default="text", init=False)

    def as_mapping(self) -> dict[str, object]:
        return {"type": self.type, "text": self.text}


@dataclass(frozen=True, slots=True)
class ImageTurnSource:
    media_type: str
    data: str
    type: Literal["base64"] = field(default="base64", init=False)

    def as_mapping(self) -> dict[str, object]:
        return {
            "type": self.type,
            "media_type": self.media_type,
            "data": self.data,
        }


@dataclass(frozen=True, slots=True)
class ImageTurnBlock:
    source: ImageTurnSource
    type: Literal["image"] = field(default="image", init=False)

    def as_mapping(self) -> dict[str, object]:
        return {"type": self.type, "source": self.source.as_mapping()}


TurnContentBlock: TypeAlias = TextTurnBlock | ImageTurnBlock
TurnContent: TypeAlias = str | tuple[TurnContentBlock, ...]


@dataclass(frozen=True, slots=True)
class TurnMessage:
    role: str
    content: TurnContent

    def as_mapping(self) -> dict[str, object]:
        content: object = self.content
        if isinstance(content, tuple):
            content = [block.as_mapping() for block in content]
        return {"role": self.role, "content": content}


@dataclass(frozen=True, slots=True)
class TurnCommand:
    messages: tuple[TurnMessage, ...]
    session_id: str
    turn_id: str
    is_cancelled: AsyncCancellationProbe
    charts_mode: bool = False
    billing_user_id: str | None = None


@dataclass(frozen=True, slots=True)
class TurnProgress:
    execution_id: str
    progress: ExecutionProgress


@dataclass(frozen=True, slots=True)
class TurnResult:
    session_id: str
    turn_id: str
    outcome: TurnOutcome
    usage_entries: tuple[ModelUsageEntry, ...] = ()
    trace: AnalysisTrace | None = None
    finalization: FinalizationResult | None = None


TurnServiceEvent: TypeAlias = TurnProgress | TurnResult


def _package_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


def current_runtime_versions() -> RuntimeVersions:
    try:
        from engine.constants import UK_CHAT_DATASET

        dataset_identifier = UK_CHAT_DATASET.name
    except Exception:
        dataset_identifier = "unknown"
    return RuntimeVersions(
        catalogue_version=_package_version("policyengine-uk"),
        engine_version=_package_version("policyengine"),
        country_package_version=_package_version("policyengine-uk"),
        dataset_identifier=dataset_identifier,
    )


def _latest_user_text(messages: tuple[TurnMessage, ...]) -> str:
    for message in reversed(messages):
        if message.role != "user":
            continue
        content = message.content
        if isinstance(content, str):
            return content
        return " ".join(
            block.text for block in content if isinstance(block, TextTurnBlock)
        )
    return ""


def _interpretation_usage_entries(
    turn: TurnCommand,
    result: InterpretationResult,
    *,
    sequence_start: int,
) -> tuple[ModelUsageEntry, ...]:
    calls = result.call_usages or (result.usage,)
    return _interpretation_call_usage_entries(
        turn,
        calls,
        sequence_start=sequence_start,
    )


def _interpretation_call_usage_entries(
    turn: TurnCommand,
    calls: tuple[InterpretationUsage, ...],
    *,
    sequence_start: int,
) -> tuple[ModelUsageEntry, ...]:
    return tuple(
        ModelUsageEntry(
            usage_entry_id=stable_identifier(
                "model_usage",
                turn.session_id,
                turn.turn_id,
                "interpretation",
                sequence_start + index,
            ),
            session_id=SessionId(turn.session_id),
            turn_id=TurnId(turn.turn_id),
            operation="interpretation",
            model=INTERPRETER_MODEL,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cache_creation_input_tokens=usage.cache_creation_input_tokens,
            cache_read_input_tokens=usage.cache_read_input_tokens,
        )
        for index, usage in enumerate(calls)
    )


def _narration_usage_entries(
    turn: TurnCommand,
    result: NarrationResult,
    *,
    sequence_start: int,
) -> tuple[ModelUsageEntry, ...]:
    calls = result.call_usages or (result.usage,)
    return _narration_call_usage_entries(
        turn,
        calls,
        model=result.model,
        sequence_start=sequence_start,
    )


def _narration_call_usage_entries(
    turn: TurnCommand,
    calls: tuple[dict[str, int], ...],
    *,
    model: str,
    sequence_start: int,
) -> tuple[ModelUsageEntry, ...]:
    return tuple(
        ModelUsageEntry(
            usage_entry_id=stable_identifier(
                "model_usage",
                turn.session_id,
                turn.turn_id,
                "narration",
                sequence_start + index,
            ),
            session_id=SessionId(turn.session_id),
            turn_id=TurnId(turn.turn_id),
            operation="narration",
            model=model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            cache_creation_input_tokens=usage.get("cache_creation_input_tokens", 0),
            cache_read_input_tokens=usage.get("cache_read_input_tokens", 0),
        )
        for index, usage in enumerate(calls)
    )


@dataclass(frozen=True)
class TurnServiceDependencies:
    store: AnalysisStore
    interpreter: TurnInterpreter = interpret_turn
    request_compiler: RequestCompilationService = field(
        default_factory=RequestCompiler.runtime_default
    )
    execution_engine: ExecutionService = field(default_factory=ExecutionEngine)
    billing_intent_builder: BillingIntentBuilder | None = None
    narrator: NarrationService = narrate_execution_result
    versions: RuntimeVersionProvider = current_runtime_versions


def _finalize_analysis_turn(
    *,
    dependencies: TurnServiceDependencies,
    store: AnalysisStore,
    receipt: TurnReceipt,
    transition: WorkflowTransition,
    outcome: TurnOutcome,
    usage_entries: tuple[ModelUsageEntry, ...] = (),
    trace: AnalysisTrace | None = None,
    billing_user_id: str | None = None,
) -> FinalizationResult:
    billing_intent = None
    if (
        dependencies.billing_intent_builder is not None
        and usage_entries
        and outcome_is_billable(outcome)
    ):
        billing_intent = dependencies.billing_intent_builder(
            session_id=str(receipt.session_id),
            turn_id=str(receipt.turn_id),
            user_id=billing_user_id,
            usage_entries=usage_entries,
        )
    return finalize_turn(
        store=store,
        receipt=receipt,
        transition=transition,
        outcome=outcome,
        usage_entries=usage_entries,
        trace=trace,
        billing_intent=billing_intent,
    )


async def _attempt_monitor(
    *,
    external_probe: AsyncCancellationProbe,
    store: AnalysisStore,
    execution_id: str,
    token: str,
    cancelled: threading.Event,
) -> None:
    last_heartbeat = 0.0
    while not cancelled.is_set():
        try:
            if await external_probe() or store.cancellation_requested(
                execution_id=execution_id,
                token=token,
            ):
                cancelled.set()
                return
            now = time.monotonic()
            if now - last_heartbeat >= DEFAULT_EXECUTION_HEARTBEAT_SECONDS:
                store.heartbeat_attempt(
                    HeartbeatAttemptCommand(
                        execution_id=execution_id,
                        token=token,
                    )
                )
                last_heartbeat = now
        except AnalysisError as exc:
            if exc.code in {
                AnalysisErrorCode.EXECUTION_TOKEN_INVALID,
                AnalysisErrorCode.EXECUTION_EXPIRED,
            }:
                cancelled.set()
                return
            logger.debug("Execution monitor persistence check failed", exc_info=True)
        await asyncio.sleep(0.25)


def _recover_expired_attempts(store: AnalysisStore, session_id: str) -> tuple[str, ...]:
    """Reduce and commit recovery transitions outside the persistence boundary."""
    recovered: list[str] = []
    recovered_at = datetime.now(timezone.utc)
    for attempt in store.expired_attempts(at=recovered_at, session_id=session_id):
        state = store.load_state(attempt.session_id)
        if state.active_execution_id != attempt.execution_id:
            continue
        transition = LifecycleReducer.reduce(
            state,
            RecoveryEvent(attempt=attempt, recovered_at=recovered_at),
        )
        try:
            store.commit_transition(transition)
        except AnalysisError as exc:
            if exc.code == AnalysisErrorCode.STATE_CONFLICT:
                continue
            raise
        recovered.append(attempt.execution_id)
    return tuple(recovered)


async def _wait_for_replacement_plan(
    *,
    store: AnalysisStore,
    session_id: str,
    plan_id: str,
    is_cancelled: AsyncCancellationProbe,
) -> AnalysisSessionState | None:
    """Keep the replacement request responsible until its plan is claimable."""

    deadline = time.monotonic() + REPLACEMENT_WAIT_TIMEOUT_SECONDS
    last_recovery = 0.0
    while True:
        if await is_cancelled():
            return None
        now = time.monotonic()
        if now - last_recovery >= 1.0:
            _recover_expired_attempts(store, session_id)
            last_recovery = now
        state = store.load_state(session_id)
        if (
            state.phase.value == "ready"
            and state.active_plan_id == plan_id
            and state.active_execution_id is None
            and state.pending_plan_id is None
        ):
            return state
        if plan_id not in {state.active_plan_id, state.pending_plan_id}:
            raise AnalysisError(
                AnalysisErrorCode.STATE_CONFLICT,
                "the pending replacement was superseded before it became ready",
                retryable=True,
            )
        if now >= deadline:
            return None
        await asyncio.sleep(0.1)


def _trace(
    *,
    state_version: int,
    interpretation: InterpretationResult | None = None,
    revision: SemanticRequestRevision | None = None,
    decision: RequestCompilation | None = None,
    plan: ExecutionPlan | None = None,
    execution: ExecutionResult | None = None,
    conflicts: int = 0,
    usage_entries: tuple[ModelUsageEntry, ...] = (),
) -> AnalysisTrace:
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    for entry in usage_entries:
        usage["input_tokens"] += entry.input_tokens
        usage["output_tokens"] += entry.output_tokens
        usage["cache_creation_input_tokens"] += entry.cache_creation_input_tokens
        usage["cache_read_input_tokens"] += entry.cache_read_input_tokens
    return AnalysisTrace(
        workflow_version=state_version,
        update_kind=(interpretation.validated_update.kind if interpretation else None),
        revision_relationship=revision.relationship if revision else None,
        inherited_fields=tuple(
            name
            for name, field in (revision.fields.items() if revision else ())
            if field.provenance.value == "inherited"
        ),
        binding_outcome=type(decision).__name__ if decision else None,
        clarification_id=(
            decision.clarification.question_id
            if isinstance(decision, CompilationClarification)
            else None
        ),
        plan_id=plan.plan_id if plan else None,
        plan_hash=plan.plan_hash if plan else None,
        execution_mode=plan.mode.value if plan else None,
        permitted_operations=plan.allowed_operations if plan else (),
        step_status=tuple(
            (item.step_id, item.status)
            for item in (execution.completion.operations if execution else ())
        ),
        conflict_count=conflicts,
        interpretation_retries=interpretation.retry_count if interpretation else 0,
        model_usage=usage,
    )


def _complete_turn(
    *,
    dependencies: TurnServiceDependencies,
    store: AnalysisStore,
    turn: TurnCommand,
    outcome: TurnOutcome,
    usage_entries: tuple[ModelUsageEntry, ...] = (),
    trace: AnalysisTrace | None = None,
    receipt: TurnReceipt | None = None,
    transition: WorkflowTransition | None = None,
) -> TurnResult:
    """Build every final service result and persist it when a receipt exists."""

    if (receipt is None) != (transition is None):
        raise AnalysisError(
            AnalysisErrorCode.OUTCOME_INVALID,
            "turn completion requires both a receipt and lifecycle transition",
        )
    finalization = None
    if receipt is not None and transition is not None:
        finalization = _finalize_analysis_turn(
            dependencies=dependencies,
            store=store,
            receipt=receipt,
            transition=transition,
            outcome=outcome,
            usage_entries=usage_entries,
            trace=trace,
            billing_user_id=turn.billing_user_id,
        )
    return TurnResult(
        session_id=turn.session_id,
        turn_id=turn.turn_id,
        outcome=outcome,
        usage_entries=usage_entries,
        trace=trace,
        finalization=finalization,
    )


async def _run_turn(
    turn: TurnCommand,
    *,
    dependencies: TurnServiceDependencies,
) -> AsyncIterator[TurnServiceEvent]:
    """Run one user message through all typed analysis stages."""

    store = dependencies.store
    outcome: TurnOutcome
    message = _latest_user_text(turn.messages)
    if not message.strip():
        outcome = FailedTurnOutcome(
            content="Please send a user message to continue.",
            error_code="invalid_request",
        )
        yield _complete_turn(
            dependencies=dependencies,
            store=store,
            turn=turn,
            outcome=outcome,
            usage_entries=(),
            trace=None,
        )
        return

    try:
        _recover_expired_attempts(store, turn.session_id)
        loaded = store.load_or_create(
            LoadOrCreateSessionCommand(session_id=turn.session_id)
        )
        started = store.begin_turn(
            BeginTurnCommand(
                session_id=turn.session_id,
                turn_id=turn.turn_id,
                request_content={
                    "messages": [message.as_mapping() for message in turn.messages],
                    "charts_mode": turn.charts_mode,
                },
                state_version=loaded.state.state_version,
            )
        )
    except AnalysisError as exc:
        if exc.code != AnalysisErrorCode.IDEMPOTENCY_CONFLICT:
            raise
        outcome = ConflictTurnOutcome(
            content=(
                "This turn identifier is already associated with different request "
                "content. Submit this request with a new turn identifier."
            ),
            retryable=True,
        )
        yield _complete_turn(
            dependencies=dependencies,
            store=store,
            turn=turn,
            outcome=outcome,
            usage_entries=(),
            trace=None,
        )
        return
    if started.duplicate:
        receipt_created_at = started.receipt.created_at
        if receipt_created_at.tzinfo is None:
            receipt_created_at = receipt_created_at.replace(tzinfo=timezone.utc)
        if (
            started.receipt.status.value == "processing"
            and receipt_created_at
            <= datetime.now(timezone.utc)
            - timedelta(seconds=DEFAULT_PROCESSING_RECEIPT_TIMEOUT_SECONDS)
        ):
            current = store.load_state(turn.session_id)
            transition = LifecycleReducer.reduce(current, ConflictObservedEvent())
            outcome = ConflictTurnOutcome(
                content=(
                    "The earlier processing attempt expired before it produced a "
                    "final response. Submit this request with a new turn identifier."
                ),
                retryable=True,
            )
            yield _complete_turn(
                dependencies=dependencies,
                store=store,
                turn=turn,
                receipt=started.receipt,
                transition=transition,
                outcome=outcome,
            )
            return
        outcome = replay_outcome(started.receipt)
        yield _complete_turn(
            dependencies=dependencies,
            store=store,
            turn=turn,
            outcome=outcome,
            usage_entries=(),
            trace=_trace(state_version=started.receipt.state_version),
        )
        return

    if await turn.is_cancelled():
        transition = LifecycleReducer.reduce(
            loaded.state,
            ConversationAdvancedEvent(occurred_at=datetime.now(timezone.utc)),
        )
        outcome = CancelledTurnOutcome(
            content="This turn was cancelled before analysis began.",
            request_revision_id=loaded.state.active_revision_id,
        )
        # Transport cancellation does not cancel a previously active analysis.
        yield _complete_turn(
            dependencies=dependencies,
            store=store,
            turn=turn,
            receipt=started.receipt,
            transition=transition,
            outcome=outcome,
        )
        return

    usage_entries: list[ModelUsageEntry] = []
    conflicts = 0
    runtime_versions = dependencies.versions()
    loop = asyncio.get_running_loop()
    attempt_claimed = False

    for interpretation_attempt in range(2):
        if interpretation_attempt:
            loaded = store.load(turn.session_id)
        context = InterpreterContext(
            state=loaded.state,
            active_revision=loaded.active_revision,
            active_clarification=loaded.active_clarification,
            executions={
                key: value
                for key, value in loaded.executions.items()
                if isinstance(value, ExecutionAttempt)
            },
            latest_user_message=message,
            recent_messages=tuple(
                message.as_mapping() for message in turn.messages[-5:-1]
            ),
        )
        try:
            try:
                interpretation = await loop.run_in_executor(
                    None, partial(dependencies.interpreter, context)
                )
            except InterpretationFailure as exc:
                usage_entries.extend(
                    _interpretation_call_usage_entries(
                        turn,
                        exc.call_usages,
                        sequence_start=len(usage_entries),
                    )
                )
                raise
            new_usage = _interpretation_usage_entries(
                turn,
                interpretation,
                sequence_start=len(usage_entries),
            )
            usage_entries.extend(new_usage)
            update = interpretation.validated_update

            if isinstance(update, ValidatedAskAboutExecution):
                prior_attempt = loaded.executions[str(update.execution_id)]
                if not isinstance(prior_attempt, ExecutionAttempt):
                    raise AnalysisError(
                        AnalysisErrorCode.STATE_PRECONDITION_FAILED,
                        "execution details are unavailable for this question",
                    )
                prior_plan = store.load_plan(
                    turn.session_id,
                    str(prior_attempt.plan_id),
                )
                prior_revision = store.load_revision(
                    turn.session_id,
                    str(prior_attempt.request_revision_id),
                )
                content = answer_execution_question(
                    question=update.question,
                    revision=prior_revision,
                    plan=prior_plan,
                    execution=prior_attempt,
                )
                transition = LifecycleReducer.reduce(
                    loaded.state,
                    ConversationAdvancedEvent(occurred_at=datetime.now(timezone.utc)),
                )
                outcome = CompletedTurnOutcome(
                    content=content,
                    route="execution_question",
                    model=INTERPRETER_MODEL,
                )
                trace = _trace(
                    state_version=transition.next_state.state_version,
                    interpretation=interpretation,
                    conflicts=conflicts,
                    usage_entries=tuple(usage_entries),
                )
                yield _complete_turn(
                    dependencies=dependencies,
                    store=store,
                    turn=turn,
                    receipt=started.receipt,
                    transition=transition,
                    outcome=outcome,
                    usage_entries=tuple(usage_entries),
                    trace=trace,
                )
                return

            if isinstance(update, ValidatedCancelAnalysis):
                transition = LifecycleReducer.reduce(
                    loaded.state,
                    CancellationRequestedEvent(
                        request_revision_id=update.request_revision_id,
                        prior_clarification=loaded.active_clarification,
                        resolving_turn_id=turn.turn_id,
                        occurred_at=datetime.now(timezone.utc),
                    ),
                )
                outcome = CancelledTurnOutcome(
                    content="The active analysis request has been cancelled.",
                    request_revision_id=update.request_revision_id,
                    model=INTERPRETER_MODEL,
                )
                trace = _trace(
                    state_version=transition.next_state.state_version,
                    interpretation=interpretation,
                    conflicts=conflicts,
                    usage_entries=tuple(usage_entries),
                )
                yield _complete_turn(
                    dependencies=dependencies,
                    store=store,
                    turn=turn,
                    receipt=started.receipt,
                    transition=transition,
                    outcome=outcome,
                    usage_entries=tuple(usage_entries),
                    trace=trace,
                )
                return

            decision = await loop.run_in_executor(
                None,
                partial(
                    dependencies.request_compiler.compile,
                    CompilationInput(
                        update=update,
                        state=loaded.state,
                        current_revision=loaded.active_revision,
                        active_clarification=loaded.active_clarification,
                        turn_id=turn.turn_id,
                        runtime_versions=runtime_versions,
                        bootstrap=(
                            loaded.active_revision is None and len(turn.messages) > 1
                        ),
                        created_at=datetime.now(timezone.utc),
                    ),
                ),
            )
            usage_entries.extend(decision.usage_entries)
            revision = decision.revision
            if isinstance(decision, CompilationClarification):
                revision = decision.revision
                clarification = decision.clarification
                transition = LifecycleReducer.reduce(
                    loaded.state,
                    ClarificationRequiredEvent(
                        revision=revision,
                        clarification=clarification,
                        prior_clarification=loaded.active_clarification,
                        resolving_turn_id=turn.turn_id,
                        answer_submitted=isinstance(
                            update, ValidatedAnswerClarification
                        ),
                    ),
                )
                clarification = transition.clarifications[0]
                outcome = ClarificationTurnOutcome(
                    content=render_clarification(clarification),
                    question_id=clarification.question_id,
                    reason_code=clarification.reason_code,
                    model=INTERPRETER_MODEL,
                )
                trace = _trace(
                    state_version=transition.next_state.state_version,
                    interpretation=interpretation,
                    revision=revision,
                    decision=decision,
                    conflicts=conflicts,
                    usage_entries=tuple(usage_entries),
                )
                yield _complete_turn(
                    dependencies=dependencies,
                    store=store,
                    turn=turn,
                    receipt=started.receipt,
                    transition=transition,
                    outcome=outcome,
                    usage_entries=tuple(usage_entries),
                    trace=trace,
                )
                return

            if isinstance(
                decision,
                (RequestUnsupported, RequestCompilationFailed),
            ):
                failed = isinstance(decision, RequestCompilationFailed)
                if revision is None:
                    transition = LifecycleReducer.reduce(
                        loaded.state,
                        TurnFailedEvent(occurred_at=datetime.now(timezone.utc)),
                    )
                else:
                    transition = LifecycleReducer.reduce(
                        loaded.state,
                        RequestRejectedEvent(
                            revision=revision,
                            outcome="failed" if failed else "unsupported",
                            prior_clarification=loaded.active_clarification,
                            resolving_turn_id=turn.turn_id,
                            answer_submitted=isinstance(
                                update, ValidatedAnswerClarification
                            ),
                        ),
                    )
                if isinstance(decision, RequestCompilationFailed):
                    outcome = FailedTurnOutcome(
                        content=decision.reason,
                        error_code=decision.error_code.value,
                        model=INTERPRETER_MODEL,
                        billable=True,
                    )
                else:
                    outcome = UnsupportedTurnOutcome(
                        content=decision.reason,
                        reason_code="unsupported_scope",
                        model=INTERPRETER_MODEL,
                    )
                trace = _trace(
                    state_version=transition.next_state.state_version,
                    interpretation=interpretation,
                    revision=revision,
                    decision=decision,
                    conflicts=conflicts,
                    usage_entries=tuple(usage_entries),
                )
                yield _complete_turn(
                    dependencies=dependencies,
                    store=store,
                    turn=turn,
                    receipt=started.receipt,
                    transition=transition,
                    outcome=outcome,
                    usage_entries=tuple(usage_entries),
                    trace=trace,
                )
                return

            if not isinstance(decision, CompiledRequest):
                raise AnalysisError(
                    AnalysisErrorCode.BINDING_FAILED,
                    "request compiler returned an unknown decision",
                )
            revision = decision.revision
            bound_request = decision.bound_request
            plan = decision.plan
            plan_transition = LifecycleReducer.reduce(
                loaded.state,
                PlanReadyEvent(
                    revision=revision,
                    bound_request=bound_request,
                    plan=plan,
                    prior_clarification=loaded.active_clarification,
                    resolving_turn_id=turn.turn_id,
                    answer_submitted=isinstance(
                        update, ValidatedAnswerClarification
                    ),
                ),
            )
            if loaded.state.active_execution_id is not None:
                store.commit_transition(plan_transition)
                ready_state = await _wait_for_replacement_plan(
                    store=store,
                    session_id=turn.session_id,
                    plan_id=str(plan.plan_id),
                    is_cancelled=turn.is_cancelled,
                )
                if ready_state is None:
                    current = store.load_state(turn.session_id)
                    transition = LifecycleReducer.reduce(
                        current,
                        CancellationRequestedEvent(
                            request_revision_id=revision.revision_id,
                            occurred_at=datetime.now(timezone.utc),
                        ),
                    )
                    outcome = CancelledTurnOutcome(
                        content=(
                            "The replacement calculation was cancelled before it "
                            "started."
                        ),
                        request_revision_id=revision.revision_id,
                        model=INTERPRETER_MODEL,
                    )
                    trace = _trace(
                        state_version=transition.next_state.state_version,
                        interpretation=interpretation,
                        revision=revision,
                        decision=decision,
                        plan=plan,
                        conflicts=conflicts,
                        usage_entries=tuple(usage_entries),
                    )
                    yield _complete_turn(
                        dependencies=dependencies,
                        store=store,
                        turn=turn,
                        receipt=started.receipt,
                        transition=transition,
                        outcome=outcome,
                        usage_entries=tuple(usage_entries),
                        trace=trace,
                    )
                    return
            else:
                ready_state = store.commit_transition(plan_transition)
            if plan.mode == ExecutionMode.EXPLANATION:
                try:
                    narrated = await loop.run_in_executor(
                        None,
                        partial(
                            dependencies.narrator,
                            revision=revision,
                            plan=plan,
                            summaries=(),
                            facts=FactRegister(),
                        ),
                    )
                except Exception as exc:
                    if isinstance(exc, NarrationFailure):
                        usage_entries.extend(
                            _narration_call_usage_entries(
                                turn,
                                exc.call_usages,
                                model=NARRATION_MODEL,
                                sequence_start=len(usage_entries),
                            )
                        )
                    logger.exception("Explanation narration failed")
                    outcome = FailedTurnOutcome(
                        content="The explanation could not be validated. Please try again.",
                        error_code=AnalysisErrorCode.NARRATION_INVALID.value,
                        model=NARRATION_MODEL,
                        billable=bool(usage_entries),
                    )
                    final_transition = LifecycleReducer.reduce(
                        ready_state,
                        ExplanationOutcomeEvent(
                            plan_id=plan.plan_id,
                            status="failed",
                            completed_at=datetime.now(timezone.utc),
                        ),
                    )
                    trace = _trace(
                        state_version=final_transition.next_state.state_version,
                        interpretation=interpretation,
                        revision=revision,
                        decision=decision,
                        plan=plan,
                        conflicts=conflicts,
                        usage_entries=tuple(usage_entries),
                    )
                    yield _complete_turn(
                        dependencies=dependencies,
                        store=store,
                        turn=turn,
                        receipt=started.receipt,
                        transition=final_transition,
                        outcome=outcome,
                        usage_entries=tuple(usage_entries),
                        trace=trace,
                    )
                    return
                if isinstance(narrated, NarrationResult):
                    content = narrated.content
                    response_model = narrated.model
                    usage_entries.extend(
                        _narration_usage_entries(
                            turn,
                            narrated,
                            sequence_start=len(usage_entries),
                        )
                    )
                else:
                    content = str(narrated)
                    response_model = NARRATION_MODEL
                outcome = CompletedTurnOutcome(
                    content=content,
                    route="explanation",
                    model=response_model,
                )
                final_transition = LifecycleReducer.reduce(
                    ready_state,
                    ExplanationOutcomeEvent(
                        plan_id=plan.plan_id,
                        status="completed",
                        completed_at=datetime.now(timezone.utc),
                    ),
                )
                trace = _trace(
                    state_version=final_transition.next_state.state_version,
                    interpretation=interpretation,
                    revision=revision,
                    decision=decision,
                    plan=plan,
                    conflicts=conflicts,
                    usage_entries=tuple(usage_entries),
                )
                yield _complete_turn(
                    dependencies=dependencies,
                    store=store,
                    turn=turn,
                    receipt=started.receipt,
                    transition=final_transition,
                    outcome=outcome,
                    usage_entries=tuple(usage_entries),
                    trace=trace,
                )
                return

            claim_token = secrets.token_urlsafe(32)
            claimed_at = datetime.now(timezone.utc)
            claim = store.commit_plan_claim(
                build_plan_claim_command(
                    state=ready_state,
                    plan=plan,
                    execution_id=stable_identifier(
                        "execution",
                        turn.session_id,
                        plan.plan_id,
                        secrets.token_hex(16),
                    ),
                    token=claim_token,
                    worker_id=stable_identifier(
                        "worker", turn.session_id, turn.turn_id
                    ),
                    claimed_at=claimed_at,
                    lease_seconds=DEFAULT_EXECUTION_LEASE_SECONDS,
                )
            )
            attempt_claimed = True
            event_queue: asyncio.Queue[ExecutionProgress] = asyncio.Queue()
            cancelled = threading.Event()

            def on_operation_event(event: ExecutionProgress) -> None:
                loop.call_soon_threadsafe(event_queue.put_nowait, event)

            monitor = asyncio.create_task(
                _attempt_monitor(
                    external_probe=turn.is_cancelled,
                    store=store,
                    execution_id=str(claim.attempt.execution_id),
                    token=claim.token,
                    cancelled=cancelled,
                )
            )
            execution_request = ExecutionRequest(
                plan=plan,
                attempt=claim.attempt,
                token=claim.token,
                revision=revision,
                bound_request=bound_request,
                control=CallbackExecutionControl(
                    attempt_verifier=lambda execution_id, token: store.verify_attempt(
                        execution_id=execution_id,
                        token=token,
                        plan=plan,
                    ),
                    cancellation_probe=cancelled.is_set,
                    progress_sink=on_operation_event,
                ),
            )
            execution_future = loop.run_in_executor(
                None,
                partial(
                    dependencies.execution_engine.execute,
                    execution_request,
                ),
            )
            try:
                while not execution_future.done() or not event_queue.empty():
                    try:
                        operation_event = await asyncio.wait_for(
                            event_queue.get(), timeout=0.1
                        )
                    except asyncio.TimeoutError:
                        continue
                    yield TurnProgress(
                        execution_id=str(claim.attempt.execution_id),
                        progress=operation_event,
                    )
                execution_result = await execution_future
            except Exception as exc:
                logger.exception("Claimed execution failed before returning an outcome")
                code = exc.code if isinstance(exc, AnalysisError) else AnalysisErrorCode.EXECUTION_FAILED
                execution_result = failed_execution_result(
                    execution_request,
                    code,
                )
            finally:
                cancellation_observed = cancelled.is_set()
                cancelled.set()
                monitor.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await monitor

            usage_entries.extend(execution_result.usage_entries)
            completion = execution_result.completion
            current_state = store.load_state(turn.session_id)
            current_attempt = store.load_attempt(str(claim.attempt.execution_id))
            if completion.status == "completed" and (
                cancellation_observed
                or current_attempt.status
                == ExecutionAttemptStatus.CANCELLATION_REQUESTED
            ):
                completion = ExecutionCompletion(
                    execution_id=claim.attempt.execution_id,
                    status=(
                        "superseded"
                        if current_state.pending_plan_id is not None
                        else "cancelled"
                    ),
                    operations=completion.operations,
                    error_code=AnalysisErrorCode.EXECUTION_CANCELLED.value,
                )
            if completion.status == "completed":
                try:
                    narrated = await loop.run_in_executor(
                        None,
                        partial(
                            dependencies.narrator,
                            revision=revision,
                            plan=plan,
                            summaries=execution_result.record.operation_summaries,
                            facts=execution_result.record.fact_register,
                            caveats=execution_result.record.caveats,
                        ),
                    )
                    if isinstance(narrated, NarrationResult):
                        content = narrated.content
                        response_model = narrated.model
                        usage_entries.extend(
                            _narration_usage_entries(
                                turn,
                                narrated,
                                sequence_start=len(usage_entries),
                            )
                        )
                    else:
                        content = str(narrated)
                        response_model = NARRATION_MODEL
                    outcome = CompletedTurnOutcome(
                        content=content,
                        route=plan.mode.value,
                        model=response_model,
                        response_artifacts=execution_result.record.response_artifacts,
                    )
                except Exception as exc:
                    if isinstance(exc, NarrationFailure):
                        usage_entries.extend(
                            _narration_call_usage_entries(
                                turn,
                                exc.call_usages,
                                model=NARRATION_MODEL,
                                sequence_start=len(usage_entries),
                            )
                        )
                    logger.exception("Narration failed after calculation")
                    completion = ExecutionCompletion(
                        execution_id=claim.attempt.execution_id,
                        status="failed",
                        operations=completion.operations,
                        error_code=AnalysisErrorCode.NARRATION_INVALID.value,
                    )
                    outcome = FailedTurnOutcome(
                        content="The calculation completed, but its response could not be validated.",
                        error_code=AnalysisErrorCode.NARRATION_INVALID.value,
                        model=NARRATION_MODEL,
                        billable=True,
                    )
            elif completion.status in {"cancelled", "superseded"}:
                outcome = CancelledTurnOutcome(
                    content="The calculation stopped before its next operation.",
                    request_revision_id=revision.revision_id,
                    model=(
                        EXPLORATORY_MODEL
                        if plan.mode == ExecutionMode.EXPLORATORY
                        else INTERPRETER_MODEL
                    ),
                )
            else:
                outcome = FailedTurnOutcome(
                    content="The calculation could not produce a valid result.",
                    error_code=completion.error_code or AnalysisErrorCode.EXECUTION_FAILED.value,
                    model=(
                        EXPLORATORY_MODEL
                        if plan.mode == ExecutionMode.EXPLORATORY
                        else INTERPRETER_MODEL
                    ),
                    billable=True,
                )

            final_transition = LifecycleReducer.reduce(
                current_state,
                AttemptOutcomeEvent(
                    attempt=current_attempt,
                    completion=completion,
                    completed_at=datetime.now(timezone.utc),
                ),
            )
            trace = _trace(
                state_version=final_transition.next_state.state_version,
                interpretation=interpretation,
                revision=revision,
                decision=decision,
                plan=plan,
                execution=execution_result,
                conflicts=conflicts,
                usage_entries=tuple(usage_entries),
            )
            yield _complete_turn(
                dependencies=dependencies,
                store=store,
                turn=turn,
                receipt=started.receipt,
                transition=final_transition,
                outcome=outcome,
                usage_entries=tuple(usage_entries),
                trace=trace,
            )
            return
        except AnalysisError as exc:
            if (
                exc.code == AnalysisErrorCode.STATE_CONFLICT
                and interpretation_attempt == 0
                and not attempt_claimed
            ):
                conflicts += 1
                continue
            if exc.code == AnalysisErrorCode.STATE_CONFLICT:
                outcome = ConflictTurnOutcome(content=CONFLICT_RESPONSE_CONTENT)
                state = store.load_state(turn.session_id)
                transition = LifecycleReducer.reduce(
                    state,
                    ConflictObservedEvent(),
                )
            else:
                state = store.load_state(turn.session_id)
                transition = LifecycleReducer.reduce(
                    state,
                    TurnFailedEvent(occurred_at=datetime.now(timezone.utc)),
                )
                outcome = FailedTurnOutcome(
                    content=str(exc),
                    error_code=exc.code.value,
                    retryable=exc.retryable,
                    model=INTERPRETER_MODEL,
                    billable=bool(usage_entries),
                )
            trace = _trace(
                state_version=transition.next_state.state_version,
                conflicts=conflicts,
                usage_entries=tuple(usage_entries),
            )
            yield _complete_turn(
                dependencies=dependencies,
                store=store,
                turn=turn,
                receipt=started.receipt,
                transition=transition,
                outcome=outcome,
                usage_entries=tuple(usage_entries),
                trace=trace,
            )
            return
        except Exception:
            logger.exception("Unexpected policy-analysis coordinator failure")
            state = store.load_state(turn.session_id)
            transition = LifecycleReducer.reduce(
                state,
                TurnFailedEvent(occurred_at=datetime.now(timezone.utc)),
            )
            outcome = FailedTurnOutcome(
                content="The analysis could not complete. Please try again.",
                error_code=AnalysisErrorCode.EXECUTION_FAILED.value,
                model=INTERPRETER_MODEL,
                billable=bool(usage_entries),
            )
            trace = _trace(
                state_version=transition.next_state.state_version,
                conflicts=conflicts,
                usage_entries=tuple(usage_entries),
            )
            yield _complete_turn(
                dependencies=dependencies,
                store=store,
                turn=turn,
                receipt=started.receipt,
                transition=transition,
                outcome=outcome,
                usage_entries=tuple(usage_entries),
                trace=trace,
            )
            return


class AnalysisTurnService:
    """Sequence the typed analysis roles for one accepted user message."""

    def __init__(
        self,
        dependencies: TurnServiceDependencies,
    ) -> None:
        self._dependencies = dependencies

    async def run(
        self,
        command: TurnCommand,
    ) -> AsyncIterator[TurnServiceEvent]:
        async for event in _run_turn(command, dependencies=self._dependencies):
            yield event
