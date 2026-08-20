from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
import threading
from types import SimpleNamespace
from typing import Any

import pytest
from sqlmodel import Session, create_engine, select
from sqlalchemy import update
from sqlalchemy.pool import StaticPool

from analysis.binding import BindingServices
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.compiler import ExecutionPlanCompiler
from analysis.turn_service import (
    AnalysisTurnService,
    TurnCommand,
    TurnMessage,
    TurnResult,
    TurnServiceDependencies,
)
from analysis.execution_engine import ExecutionEngine, ExecutionRequest, ExecutionResult
from analysis.executor import ExecutionOutcome
from analysis.interpreter import InterpretationResult, InterpretationUsage
from analysis.models import (
    AnswerClarification,
    AskAboutExecution,
    CandidateAnalysis,
    CandidateField,
    CancelAnalysis,
    EvidenceClaim,
    EvidenceReference,
    ExecutionCompletion,
    ExecutionRecord,
    Fact,
    FactRegister,
    ResultEnvelope,
    StartAnalysis,
    StartRelationship,
    ValidatedCancelAnalysis,
    ValidatedCandidateAnalysis,
    ValidatedCandidateField,
    ValidatedAnswerClarification,
    ValidatedAskAboutExecution,
    ValidatedStartAnalysis,
)
from analysis.lifecycle import (
    CancellationRequestedEvent,
    LifecycleReducer,
    PlanReadyEvent,
)
from analysis.narration import NarrationResult
from analysis.persistence import (
    AnalysisBillingIntentRow,
    AnalysisBoundRequestRow,
    AnalysisClarificationResolutionRow,
    AnalysisClarificationRow,
    AnalysisExecutionAttemptRow,
    AnalysisExecutionRow,
    AnalysisModelUsageRow,
    AnalysisPlanRow,
    AnalysisRequestRevisionRow,
    SqlAnalysisStore,
    AnalysisTurnReceiptRow,
    AnalysisWorkflowRow,
    ensure_analysis_tables,
)
from analysis.request_compiler import (
    CompilationInput,
    RequestCompilation,
    RequestCompiler,
)
from analysis.store import BeginTurnCommand, LoadOrCreateSessionCommand
from analysis_helpers import (
    bound_request,
    claim_plan,
    create_session,
    finish_attempt,
    owned_analysis_store,
    revision,
)
from chat.events import (
    CancellationAccepted,
    ClarificationRequired,
    DuplicateProcessed,
    TurnCompleted,
    TurnConflict,
    TurnFailed,
)
from chat.analysis_adapter import run_analysis_turn
from chat.turn_input import ChatTurnInput


def _store() -> SqlAnalysisStore:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    ensure_analysis_tables(engine)
    return owned_analysis_store(engine)


def _interpretation(
    kind: str,
    *,
    outputs: tuple[str, ...] = (),
    fields: dict[str, Any] | None = None,
    relationship: StartRelationship = StartRelationship.NEW,
    related_revision_id: str | None = None,
) -> InterpretationResult:
    raw_fields = {
        name: CandidateField(value=value, evidence=EvidenceClaim(quote=str(value)))
        for name, value in (fields or {}).items()
    }
    raw = StartAnalysis(
        candidate=CandidateAnalysis(
            analysis_kind=CandidateField(
                value=kind, evidence=EvidenceClaim(quote=kind)
            ),
            fields=raw_fields,
            outputs=outputs,
            output_evidence=EvidenceClaim(quote="outputs") if outputs else None,
        ),
        relationship=relationship,
        related_revision_id=related_revision_id,
    )
    validated = ValidatedStartAnalysis(
        candidate=ValidatedCandidateAnalysis(
            analysis_kind=ValidatedCandidateField(
                value=kind,
                evidence=EvidenceReference(quote=kind, message_sha256="hash"),
            ),
            fields={
                name: ValidatedCandidateField(
                    value=value,
                    evidence=EvidenceReference(quote=str(value), message_sha256="hash"),
                )
                for name, value in (fields or {}).items()
            },
            outputs=outputs,
            output_evidence=(
                EvidenceReference(quote="outputs", message_sha256="hash")
                if outputs
                else None
            ),
        ),
        relationship=relationship,
        related_revision_id=related_revision_id,
    )
    usage = InterpretationUsage(input_tokens=3, output_tokens=2)
    return InterpretationResult(raw, validated, usage, call_usages=(usage,))


def _narrator(**_kwargs: object) -> NarrationResult:
    usage = {
        "input_tokens": 2,
        "output_tokens": 2,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
    }
    return NarrationResult("Validated response.", "claude-haiku-4-5", usage, (usage,))


def _clarification_interpretation(
    question_id: str,
    answer: tuple[str, ...],
) -> InterpretationResult:
    quote = "aggregate" if answer else "no output yet"
    raw = AnswerClarification(
        question_id=question_id,
        answer=list(answer),
        evidence=EvidenceClaim(quote=quote),
    )
    validated = ValidatedAnswerClarification(
        question_id=question_id,
        answer=tuple(answer),
        evidence=EvidenceReference(quote=quote, message_sha256="hash"),
    )
    usage = InterpretationUsage(input_tokens=3, output_tokens=2)
    return InterpretationResult(raw, validated, usage, call_usages=(usage,))


def _executor(**kwargs: Any) -> ExecutionOutcome:
    attempt = kwargs["attempt"]
    plan = kwargs["plan"]
    return ExecutionOutcome(
        completion=ExecutionCompletion(
            execution_id=attempt.execution_id,
            status="completed",
        ),
        record=ExecutionRecord(
            execution_id=attempt.execution_id,
            plan_id=plan.plan_id,
            operation_summaries=(
                {
                    "step_id": "budget",
                    "operation": "compute_budgetary_impact",
                    "summary": {"status": "success"},
                },
            ),
            fact_register=FactRegister(
                facts=(
                    Fact(
                        fact_id="fact_budget",
                        raw_value=1,
                        unit="GBP",
                        display_value="£1",
                        label="Budget effect",
                        source_step_id="budget",
                    ),
                )
            ),
        ),
        envelopes=(),
        events=(),
    )


async def _not_cancelled() -> bool:
    return False


class CountingRequestCompiler:
    def __init__(self, delegate: RequestCompiler) -> None:
        self._delegate = delegate
        self.calls = 0

    def compile(self, compilation_input: CompilationInput) -> RequestCompilation:
        self.calls += 1
        return self._delegate.compile(compilation_input)


class CountingExecutionService:
    def __init__(self, delegate: ExecutionEngine) -> None:
        self._delegate = delegate
        self.calls = 0

    def execute(self, request: ExecutionRequest) -> ExecutionResult:
        self.calls += 1
        return self._delegate.execute(request)


async def _run(turn, *, store, interpreter, **overrides):
    request_compiler = overrides.get("request_compiler") or RequestCompiler(
        binding_services=overrides.get("binding_services", BindingServices())
    )
    dependencies = TurnServiceDependencies(
        store=store,
        interpreter=interpreter,
        request_compiler=request_compiler,
        execution_engine=overrides.get(
            "execution_engine",
            ExecutionEngine(
                standard_strategy=overrides.get("standard_executor", _executor),
                exploratory_strategy=overrides.get(
                    "exploratory_executor", _executor
                ),
            ),
        ),
        narrator=overrides.get("narrator", _narrator),
    )
    return [
        event
        async for event in run_analysis_turn(
            turn,
            is_cancelled=_not_cancelled,
            dependencies=dependencies,
        )
    ]


def _cancellation_interpretation(
    request_revision_id: str | None,
) -> InterpretationResult:
    raw = CancelAnalysis(request_revision_id=request_revision_id)
    validated = ValidatedCancelAnalysis(
        request_revision_id=request_revision_id,
    )
    usage = InterpretationUsage(input_tokens=3, output_tokens=2)
    return InterpretationResult(
        raw,
        validated,
        usage,
        call_usages=(usage,),
    )


def _fake_binding_services() -> BindingServices:
    return BindingServices(
        default_year=2026,
        reform_validator=lambda reform, _year: {
            "valid": True,
            "normalized_reform": reform,
        },
        household_validator=lambda **_values: {"valid": True},
    )


def test_explanation_follows_directional_pipeline_and_finalizes_once():
    store = _store()
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "explanation"}],
        session_id="session",
        turn_id="turn",
    )
    events = asyncio.run(_run(
        turn,
        store=store,
        interpreter=lambda _context: _interpretation("explanation"),
    ))
    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.route == "explanation"
    assert done.outcome == "completed"
    assert len(done.usage_entries) == 2
    assert store.load_state("session").phase.value == "completed"
    assert store.load_receipt("session", "turn").outcome_category == "completed"


def test_clarification_uses_typed_outcome_and_replay_metadata():
    store = _store()
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "society"}],
        session_id="session",
        turn_id="turn",
    )
    calls = 0

    def interpreter(_context):
        nonlocal calls
        calls += 1
        return _interpretation("society")

    first = asyncio.run(_run(turn, store=store, interpreter=interpreter))
    assert any(isinstance(event, ClarificationRequired) for event in first)
    second = asyncio.run(_run(turn, store=store, interpreter=interpreter))
    assert calls == 1
    assert any(isinstance(event, DuplicateProcessed) for event in second)
    assert any(isinstance(event, ClarificationRequired) for event in second)


def test_incomplete_answer_to_same_clarification_target_increments_attempt():
    store = _store()
    first_turn = ChatTurnInput(
        messages=[{"role": "user", "content": "society"}],
        session_id="session",
        turn_id="turn_one",
    )
    asyncio.run(
        _run(
            first_turn,
            store=store,
            interpreter=lambda _context: _interpretation("society"),
        )
    )
    question = store.load("session").active_clarification

    asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "no output yet"}],
                session_id="session",
                turn_id="turn_two",
            ),
            store=store,
            interpreter=lambda _context: _clarification_interpretation(
                question.question_id, ()
            ),
        )
    )

    loaded = store.load("session")
    assert loaded.active_clarification.target_field == "outputs"
    assert loaded.active_clarification.attempt_count == 1


def test_answer_that_reveals_different_missing_field_resets_attempt_count():
    store = _store()
    asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society"}],
                session_id="session",
                turn_id="turn_one",
            ),
            store=store,
            interpreter=lambda _context: _interpretation("society"),
        )
    )
    question = store.load("session").active_clarification

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "aggregate"}],
                session_id="session",
                turn_id="turn_two",
            ),
            store=store,
            interpreter=lambda _context: _clarification_interpretation(
                question.question_id, ("aggregate",)
            ),
        )
    )

    assert any(isinstance(event, ClarificationRequired) for event in events)
    loaded = store.load("session")
    assert loaded.active_clarification.target_field == "variable_query"
    assert loaded.active_clarification.attempt_count == 0


def test_cancellation_before_execution_closes_the_active_request():
    store = _store()
    asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society"}],
                session_id="session",
                turn_id="turn_one",
            ),
            store=store,
            interpreter=lambda _context: _interpretation("society"),
        )
    )
    request_revision_id = store.load_state("session").active_revision_id

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "cancel it"}],
                session_id="session",
                turn_id="turn_two",
            ),
            store=store,
            interpreter=lambda _context: _cancellation_interpretation(
                request_revision_id
            ),
        )
    )

    cancellation = next(
        event for event in events if isinstance(event, CancellationAccepted)
    )
    completed = next(event for event in events if isinstance(event, TurnCompleted))
    state = store.load_state("session")
    assert cancellation.request_revision_id == request_revision_id
    assert completed.outcome == "cancelled"
    assert state.phase.value == "cancelled"
    assert state.active_execution_id is None


def test_standard_calculation_claims_executes_narrates_then_finalizes():
    store = _store()
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "society outputs"}],
        session_id="session",
        turn_id="turn",
    )
    events = asyncio.run(_run(
        turn,
        store=store,
        interpreter=lambda _context: _interpretation(
            "society", outputs=("budgetary_impact",)
        ),
    ))
    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.route == "standard"
    state = store.load_state("session")
    assert state.phase.value == "completed"
    assert state.active_execution_id is None
    assert state.latest_execution_id is not None


def test_turn_service_compiles_once_and_executes_at_most_once():
    store = _store()
    compiler = CountingRequestCompiler(
        RequestCompiler(binding_services=BindingServices())
    )
    execution = CountingExecutionService(
        ExecutionEngine(standard_strategy=_executor)
    )
    service = AnalysisTurnService(
        TurnServiceDependencies(
            store=store,
            interpreter=lambda _context: _interpretation(
                "society",
                outputs=("budgetary_impact",),
            ),
            request_compiler=compiler,
            execution_engine=execution,
            narrator=_narrator,
        )
    )
    command = TurnCommand(
        messages=(TurnMessage(role="user", content="society outputs"),),
        session_id="service_session",
        turn_id="service_turn",
        is_cancelled=_not_cancelled,
    )

    async def collect():
        return [result async for result in service.run(command)]

    results = asyncio.run(collect())
    final = next(result for result in results if isinstance(result, TurnResult))

    assert compiler.calls == 1
    assert execution.calls == 1
    assert final.outcome.kind == "completed"
    assert final.finalization is not None


def test_turn_service_does_not_retry_after_execution_finalization_conflict():
    store = _store()
    compiler = CountingRequestCompiler(
        RequestCompiler(binding_services=BindingServices())
    )
    execution = CountingExecutionService(
        ExecutionEngine(standard_strategy=_executor)
    )
    original_commit = store.commit_transition
    completion_conflicts = 0

    def conflict_on_first_completion(transition):
        nonlocal completion_conflicts
        if transition.execution_completions and completion_conflicts == 0:
            completion_conflicts += 1
            raise AnalysisError(
                AnalysisErrorCode.STATE_CONFLICT,
                "concurrent completion",
                retryable=True,
            )
        return original_commit(transition)

    store.commit_transition = conflict_on_first_completion
    service = AnalysisTurnService(
        TurnServiceDependencies(
            store=store,
            interpreter=lambda _context: _interpretation(
                "society",
                outputs=("budgetary_impact",),
            ),
            request_compiler=compiler,
            execution_engine=execution,
            narrator=_narrator,
        )
    )
    command = TurnCommand(
        messages=(TurnMessage(role="user", content="society outputs"),),
        session_id="conflict_session",
        turn_id="conflict_turn",
        is_cancelled=_not_cancelled,
    )

    async def collect():
        return [result async for result in service.run(command)]

    results = asyncio.run(collect())
    final = next(result for result in results if isinstance(result, TurnResult))

    assert completion_conflicts == 1
    assert compiler.calls == 1
    assert execution.calls == 1
    assert final.outcome.kind == "conflict"
    assert final.finalization is not None


def test_execution_question_preserves_another_active_calculation():
    store = _store()
    asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society outputs"}],
                session_id="session",
                turn_id="turn_one",
            ),
            store=store,
            interpreter=lambda _context: _interpretation(
                "society", outputs=("budgetary_impact",)
            ),
        )
    )
    completed_execution_id = str(store.load_state("session").latest_execution_id)

    semantic = revision(
        session_id="session",
        revision_id="revision_active",
        revision_number=2,
        turn_id="turn_active",
    )
    bound = bound_request(
        session_id="session",
        revision_id=semantic.revision_id,
        turn_id="turn_active",
    )
    plan = ExecutionPlanCompiler.compile(bound)
    ready = store.commit_transition(
        LifecycleReducer.reduce(
            store.load_state("session"),
            PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
        )
    )
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker_active",
    )

    evidence_claim = EvidenceClaim(quote="Which dataset did it use?")
    evidence = EvidenceReference(
        quote="Which dataset did it use?", message_sha256="hash"
    )
    interpretation = InterpretationResult(
        AskAboutExecution(
            question="Which dataset did it use?",
            evidence=evidence_claim,
            execution_id=completed_execution_id,
        ),
        ValidatedAskAboutExecution(
            question="Which dataset did it use?",
            evidence=evidence,
            execution_id=completed_execution_id,
        ),
        InterpretationUsage(input_tokens=3, output_tokens=2),
    )
    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[
                    {"role": "user", "content": "Which dataset did it use?"}
                ],
                session_id="session",
                turn_id="turn_question",
            ),
            store=store,
            interpreter=lambda _context: interpretation,
        )
    )

    completed = next(event for event in events if isinstance(event, TurnCompleted))
    current = store.load_state("session")
    assert completed.route == "execution_question"
    assert current.active_execution_id == claim.attempt.execution_id
    assert store.load_attempt(str(claim.attempt.execution_id)).status.is_active


def test_replacement_turn_waits_for_old_attempt_then_executes_promoted_plan():
    store = _store()
    semantic = revision(
        session_id="session",
        revision_id="revision_active",
        revision_number=1,
        turn_id="turn_active",
    )
    bound = bound_request(
        session_id="session",
        revision_id=semantic.revision_id,
        turn_id="turn_active",
    )
    plan = ExecutionPlanCompiler.compile(bound)
    ready = store.commit_transition(
        LifecycleReducer.reduce(
            store.load_or_create(
                LoadOrCreateSessionCommand(session_id="session")
            ).state,
            PlanReadyEvent(revision=semantic, bound_request=bound, plan=plan),
        )
    )
    claim = claim_plan(
        store,
        state=ready,
        plan=plan,
        worker_id="worker_active",
    )
    execution_calls = []
    pending_recorded = threading.Event()
    original_commit = store.commit_transition

    def observed_commit(transition):
        state = original_commit(transition)
        if state.pending_plan_id is not None:
            pending_recorded.set()
        return state

    store.commit_transition = observed_commit

    def replacement_executor(**kwargs):
        execution_calls.append(str(kwargs["plan"].plan_id))
        return _executor(**kwargs)

    async def scenario():
        task = asyncio.create_task(
            _run(
                ChatTurnInput(
                    messages=[
                        {
                            "role": "user",
                            "content": "Run the poverty calculation instead.",
                        }
                    ],
                    session_id="session",
                    turn_id="turn_replacement",
                ),
                store=store,
                interpreter=lambda _context: _interpretation(
                    "society",
                    outputs=("poverty_impact",),
                    relationship=StartRelationship.RELATED,
                    related_revision_id=semantic.revision_id,
                ),
                standard_executor=replacement_executor,
            )
        )
        assert await asyncio.to_thread(pending_recorded.wait, 2)
        queued = store.load_state("session")
        assert execution_calls == []
        assert queued.active_execution_id == claim.attempt.execution_id
        assert queued.pending_plan_id is not None
        assert (
            store.load_attempt(str(claim.attempt.execution_id)).status.value
            == "cancellation_requested"
        )
        finish_attempt(
            store,
            state=queued,
            attempt=claim.attempt,
            token=claim.token,
            completion=ExecutionCompletion(
                execution_id=claim.attempt.execution_id,
                status="cancelled",
            ),
        )
        return await task

    events = asyncio.run(scenario())

    completed = next(event for event in events if isinstance(event, TurnCompleted))
    current = store.load_state("session")

    assert completed.route == "standard"
    assert len(execution_calls) == 1
    assert current.phase.value == "completed"
    assert current.active_execution_id is None
    assert current.latest_execution_id != claim.attempt.execution_id


def test_execution_payload_and_result_identifier_do_not_reach_durable_rows():
    store = _store()
    payload_sentinel = "complete_calculation_payload_4cf51a8b"
    result_id_sentinel = "result_local_secret_309db257"
    numeric_sentinel = 949_173_692_083

    def executor_with_private_result(**kwargs):
        attempt = kwargs["attempt"]
        plan = kwargs["plan"]
        return ExecutionOutcome(
            completion=ExecutionCompletion(
                execution_id=attempt.execution_id,
                status="completed",
            ),
            record=ExecutionRecord(
                execution_id=attempt.execution_id,
                plan_id=plan.plan_id,
                operation_summaries=(
                    {
                        "step_id": "budget",
                        "operation": "compute_budgetary_impact",
                        "summary": {
                            "status": "success",
                            "private_payload": payload_sentinel,
                        },
                    },
                ),
                fact_register=FactRegister(
                    facts=(
                        Fact(
                            fact_id="fact_private",
                            raw_value=numeric_sentinel,
                            unit="GBP",
                            display_value="Private calculation value",
                            label="Private calculation value",
                            source_step_id="budget",
                        ),
                    )
                ),
            ),
            envelopes=(
                ResultEnvelope(
                    execution_id=attempt.execution_id,
                    source_step_id="budget",
                    result_id=result_id_sentinel,
                    result_type="budgetary_impact",
                    value={"complete_payload": payload_sentinel},
                    public_summary={"status": "success"},
                ),
            ),
            events=(),
        )

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society outputs"}],
                session_id="session-private-result",
                turn_id="turn-private-result",
            ),
            store=store,
            interpreter=lambda _context: _interpretation(
                "society", outputs=("budgetary_impact",)
            ),
            standard_executor=executor_with_private_result,
        )
    )

    assert any(isinstance(event, TurnCompleted) for event in events)
    durable_tables = (
        AnalysisWorkflowRow,
        AnalysisRequestRevisionRow,
        AnalysisBoundRequestRow,
        AnalysisClarificationRow,
        AnalysisClarificationResolutionRow,
        AnalysisPlanRow,
        AnalysisExecutionAttemptRow,
        AnalysisExecutionRow,
        AnalysisTurnReceiptRow,
        AnalysisModelUsageRow,
        AnalysisBillingIntentRow,
    )
    durable_values = []
    with Session(store.engine) as db:
        for table in durable_tables:
            for row in db.exec(select(table)).all():
                durable_values.extend(
                    str(getattr(row, column.name))
                    for column in table.__table__.columns
                )
    serialized = " ".join(durable_values)

    assert payload_sentinel not in serialized
    assert result_id_sentinel not in serialized
    assert str(numeric_sentinel) not in serialized


def test_exploratory_calculation_uses_exploratory_executor():
    store = _store()
    seen = []

    def exploratory(**kwargs):
        seen.append(kwargs["plan"].allowed_operations)
        return _executor(**kwargs)

    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "exploratory outputs"}],
        session_id="session",
        turn_id="turn",
    )
    events = asyncio.run(_run(
        turn,
        store=store,
        interpreter=lambda _context: _interpretation(
            "exploratory",
            fields={"objective": "trace effects"},
            outputs=("budgetary_impact",),
        ),
        exploratory_executor=exploratory,
    ))
    assert seen == [("compute_budgetary_impact",)]
    assert any(isinstance(event, TurnCompleted) for event in events)


@pytest.mark.parametrize(
    ("kind", "fields", "outputs", "required_operations"),
    [
        (
            "household",
            {"people": [{"age": 30}]},
            ("net_income",),
            {"validate_household", "run_household_simulation"},
        ),
        (
            "society",
            {},
            ("budgetary_impact", "poverty_impact"),
            {
                "run_society_simulation",
                "compute_budgetary_impact",
                "compute_poverty_metrics",
            },
        ),
        (
            "society",
            {"reform": {"gov.hmrc.income_tax.allowances.personal_allowance": 13000}},
            ("budgetary_impact",),
            {"run_society_simulation", "compute_budgetary_impact"},
        ),
    ],
)
def test_fake_model_and_policyengine_pipeline_covers_bound_request_families(
    kind,
    fields,
    outputs,
    required_operations,
):
    store = _store()
    plans = []

    def executor(**kwargs):
        plans.append(kwargs["plan"])
        return _executor(**kwargs)

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": f"run {kind} analysis"}],
                session_id="session",
                turn_id="turn",
            ),
            store=store,
            interpreter=lambda _context: _interpretation(
                kind,
                fields=fields,
                outputs=outputs,
            ),
            binding_services=_fake_binding_services(),
            standard_executor=executor,
        )
    )

    completed = next(event for event in events if isinstance(event, TurnCompleted))
    assert completed.outcome == "completed"
    assert required_operations.issubset(set(plans[0].allowed_operations))


def test_related_follow_up_starts_a_new_linked_simulation_revision():
    store = _store()
    first_turn = ChatTurnInput(
        messages=[{"role": "user", "content": "run the budget simulation"}],
        session_id="session",
        turn_id="turn_one",
    )
    asyncio.run(
        _run(
            first_turn,
            store=store,
            interpreter=lambda _context: _interpretation(
                "society", outputs=("budgetary_impact",)
            ),
        )
    )
    first_revision_id = store.load_state("session").active_revision_id

    def related_interpreter(context):
        assert context.active_revision.revision_id == first_revision_id
        return _interpretation(
            "society",
            outputs=("poverty_impact",),
            relationship=StartRelationship.RELATED,
            related_revision_id=first_revision_id,
        )

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[
                    {"role": "user", "content": "run the budget simulation"},
                    {"role": "assistant", "content": "done"},
                    {"role": "user", "content": "now run the related poverty simulation"},
                ],
                session_id="session",
                turn_id="turn_two",
            ),
            store=store,
            interpreter=related_interpreter,
        )
    )

    completed = next(event for event in events if isinstance(event, TurnCompleted))
    second_revision = store.load("session").active_revision
    assert completed.outcome == "completed"
    assert second_revision.base_revision_id == first_revision_id
    assert second_revision.relationship == StartRelationship.RELATED.value


def test_duplicate_processing_performs_no_model_or_execution_work():
    store = _store()
    state = create_session(store, "session")
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "explanation"}],
        session_id="session",
        turn_id="turn",
    )
    store.begin_turn(
        BeginTurnCommand(
            session_id="session",
            turn_id="turn",
            request_content={"messages": turn.messages, "charts_mode": False},
            state_version=state.state_version,
        )
    )

    def should_not_run(_context):
        raise AssertionError("duplicate processing must not invoke interpreter")

    events = asyncio.run(_run(turn, store=store, interpreter=should_not_run))
    done = next(event for event in events if isinstance(event, TurnCompleted))
    assert done.outcome == "still_processing"
    assert done.processed_duplicate is True


def test_stale_processing_receipt_is_closed_as_retryable_conflict():
    store = _store()
    state = create_session(store, "session")
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "explanation"}],
        session_id="session",
        turn_id="turn",
    )
    store.begin_turn(
        BeginTurnCommand(
            session_id="session",
            turn_id="turn",
            request_content={"messages": turn.messages, "charts_mode": False},
            state_version=state.state_version,
        )
    )
    with Session(store.engine) as database:
        database.exec(
            update(AnalysisTurnReceiptRow)
            .where(
                AnalysisTurnReceiptRow.session_id == "session",
                AnalysisTurnReceiptRow.turn_id == "turn",
            )
            .values(
                created_at=datetime.now(timezone.utc) - timedelta(seconds=601)
            )
        )
        database.commit()

    def should_not_run(_context):
        raise AssertionError("stale receipt recovery must not invoke interpreter")

    events = asyncio.run(_run(turn, store=store, interpreter=should_not_run))

    conflict = next(event for event in events if isinstance(event, TurnConflict))
    assert conflict.retryable is True
    assert store.load_receipt("session", "turn").status.value == "conflict"


def test_turn_identifier_content_mismatch_is_a_public_conflict():
    store = _store()
    state = create_session(store, "session")
    store.begin_turn(
        BeginTurnCommand(
            session_id="session",
            turn_id="turn",
            request_content={
                "messages": [{"role": "user", "content": "first request"}],
                "charts_mode": False,
            },
            state_version=state.state_version,
        )
    )
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "different request"}],
        session_id="session",
        turn_id="turn",
    )

    def should_not_run(_context):
        raise AssertionError("idempotency conflict must not invoke interpreter")

    events = asyncio.run(_run(turn, store=store, interpreter=should_not_run))

    conflict = next(event for event in events if isinstance(event, TurnConflict))
    assert conflict.retryable is True
    assert conflict.turn_id == "turn"
    assert store.load_receipt("session", "turn").status.value == "processing"


def test_narration_failure_closes_attempt_and_receipt_as_failed():
    store = _store()
    turn = ChatTurnInput(
        messages=[{"role": "user", "content": "society outputs"}],
        session_id="session",
        turn_id="turn",
    )

    def fail_narration(**_kwargs):
        raise RuntimeError("bad narration")

    events = asyncio.run(_run(
        turn,
        store=store,
        interpreter=lambda _context: _interpretation(
            "society", outputs=("budgetary_impact",)
        ),
        narrator=fail_narration,
    ))
    assert any(isinstance(event, TurnFailed) for event in events)
    assert store.load_state("session").phase.value == "failed"
    assert store.load_receipt("session", "turn").status.value == "failed"


def test_explanation_narration_failure_marks_the_plan_failed():
    store = _store()

    def fail_narration(**_kwargs):
        raise RuntimeError("bad explanation narration")

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "explanation"}],
                session_id="session",
                turn_id="turn_explanation",
            ),
            store=store,
            interpreter=lambda _context: _interpretation("explanation"),
            narrator=fail_narration,
        )
    )

    assert any(isinstance(event, TurnFailed) for event in events)
    state = store.load_state("session")
    with Session(store.engine) as database:
        plan_row = database.exec(
            select(AnalysisPlanRow).where(
                AnalysisPlanRow.plan_id == state.active_plan_id
            )
        ).one()
    assert state.phase.value == "failed"
    assert plan_row.status == "failed"
    assert store.load_receipt("session", "turn_explanation").status.value == "failed"


def test_executor_exception_after_claim_closes_attempt_and_receipt_as_failed():
    store = _store()

    def fail_after_claim(**_kwargs):
        raise RuntimeError("worker failed after claim")

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society outputs"}],
                session_id="session",
                turn_id="turn",
            ),
            store=store,
            interpreter=lambda _context: _interpretation(
                "society", outputs=("budgetary_impact",)
            ),
            standard_executor=fail_after_claim,
        )
    )

    assert any(isinstance(event, TurnFailed) for event in events)
    state = store.load_state("session")
    attempt = store.load_attempt(str(state.latest_execution_id))
    assert state.phase.value == "failed"
    assert state.active_execution_id is None
    assert attempt.status.value == "failed"
    assert store.load_receipt("session", "turn").status.value == "failed"


def test_cancellation_recorded_during_final_operation_prevents_narration(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'coordinator-cancellation.sqlite'}",
        connect_args={"check_same_thread": False},
    )
    ensure_analysis_tables(engine)
    store = owned_analysis_store(engine)
    narration_calls = []

    def completing_after_cancellation(**kwargs):
        current = store.load_state("session")
        store.commit_transition(
            LifecycleReducer.reduce(
                current,
                CancellationRequestedEvent(
                    request_revision_id=current.active_revision_id
                ),
            )
        )
        return _executor(**kwargs)

    def narrator_must_not_run(**_kwargs):
        narration_calls.append(True)
        return _narrator()

    events = asyncio.run(
        _run(
            ChatTurnInput(
                messages=[{"role": "user", "content": "society outputs"}],
                session_id="session",
                turn_id="turn",
            ),
            store=store,
            interpreter=lambda _context: _interpretation(
                "society", outputs=("budgetary_impact",)
            ),
            standard_executor=completing_after_cancellation,
            narrator=narrator_must_not_run,
        )
    )

    assert narration_calls == []
    assert any(isinstance(event, CancellationAccepted) for event in events)
    completed = next(event for event in events if isinstance(event, TurnCompleted))
    assert completed.outcome == "cancelled"
    assert store.load_state("session").phase.value == "cancelled"


def test_turn_service_contains_no_direct_session_state_construction():
    from pathlib import Path

    source = Path("backend/analysis/turn_service.py").read_text()
    assert "AnalysisSessionState(" not in source
    assert ".model_copy(" not in source
    assert "append_and_advance" not in source
    assert "update_turn_receipt" not in source
