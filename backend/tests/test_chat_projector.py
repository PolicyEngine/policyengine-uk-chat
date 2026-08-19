from __future__ import annotations

from itertools import product

import pytest

from analysis.execution_engine import OperationCompleted, OperationStarted
from analysis.finalization import FinalizationResult
from analysis.models import (
    AnalysisSessionState,
    CancelledTurnOutcome,
    ClarificationTurnOutcome,
    CompletedTurnOutcome,
    ConflictTurnOutcome,
    FailedTurnOutcome,
    ModelUsageEntry,
    ResponseArtifact,
    TurnReceipt,
    TurnReceiptStatus,
)
from analysis_helpers import NOW
from chat.events import (
    CancellationAccepted,
    ClarificationRequired,
    DuplicateProcessed,
    ToolCompleted,
    ToolStarted,
    ToolUsed,
    TurnCompleted,
    TurnConflict,
    TurnFailed,
)
from chat.projector import ChatEventProjector, aggregate_usage, public_events


@pytest.mark.parametrize(
    ("outcome", "event_type"),
    [
        (
            ClarificationTurnOutcome(
                content="question",
                question_id="question",
                reason_code="missing_output",
                duplicate=True,
            ),
            ClarificationRequired,
        ),
        (
            FailedTurnOutcome(
                content="failed",
                error_code="failed",
                duplicate=True,
            ),
            TurnFailed,
        ),
        (
            CancelledTurnOutcome(content="cancelled", duplicate=True),
            CancellationAccepted,
        ),
        (
            ConflictTurnOutcome(content="conflict", duplicate=True),
            TurnConflict,
        ),
    ],
)
def test_duplicate_public_events_keep_recorded_category(outcome, event_type):
    events = public_events(
        outcome=outcome,
        session_id="session",
        turn_id="turn",
    )

    assert isinstance(events[0], DuplicateProcessed)
    assert events[0].status == outcome.kind
    assert any(isinstance(event, event_type) for event in events)


def test_usage_aggregation_includes_cache_tokens_and_each_call():
    entries = tuple(
        ModelUsageEntry(
            usage_entry_id=f"usage_{index}",
            session_id="session",
            turn_id="turn",
            operation=operation,
            model=model,
            input_tokens=index,
            output_tokens=index + 1,
            cache_creation_input_tokens=index + 2,
            cache_read_input_tokens=index + 3,
        )
        for index, (operation, model) in enumerate(
            product(("interpretation",), ("model-one", "model-two")),
            start=1,
        )
    )

    usage = aggregate_usage(entries)

    assert usage.input_tokens == 3
    assert usage.output_tokens == 5
    assert usage.cache_creation_input_tokens == 7
    assert usage.cache_read_input_tokens == 9


def test_finalization_projection_preserves_live_only_artifacts():
    artifact = ResponseArtifact(
        artifact_id="chart_live",
        content="```chart\n{}\n```",
    )
    outcome = CompletedTurnOutcome(
        content="complete",
        route="standard",
        response_artifacts=(artifact,),
    )
    result = FinalizationResult(
        state=AnalysisSessionState(session_id="session", updated_at=NOW),
        receipt=TurnReceipt(
            session_id="session",
            turn_id="turn",
            request_hash="hash",
            state_version=1,
            status=TurnReceiptStatus.COMPLETED,
            outcome_category="completed",
            response_content="complete",
            created_at=NOW,
        ),
        outcome=outcome,
        usage_entries=(),
        billing_intent=None,
        live_response_artifacts=(artifact,),
        trace=None,
    )

    events = ChatEventProjector.project_finalization(result)
    completed = next(event for event in events if isinstance(event, TurnCompleted))

    assert completed.response_artifacts == (artifact,)


def test_operation_progress_projection_uses_public_values_only():
    started = ChatEventProjector.project_progress(
        "execution",
        OperationStarted(
            operation="compute_budgetary_impact",
            step_id="budget",
            public_arguments={"simulation_id": {"source_step_id": "simulation"}},
        ),
    )
    completed = ChatEventProjector.project_progress(
        "execution",
        OperationCompleted(
            operation="compute_budgetary_impact",
            step_id="budget",
            status="success",
            public_output={"status": "success"},
        ),
    )

    assert isinstance(started[0], ToolStarted)
    assert isinstance(started[1], ToolUsed)
    assert "result_id" not in str(started[1].tool_input)
    assert isinstance(completed[0], ToolCompleted)
