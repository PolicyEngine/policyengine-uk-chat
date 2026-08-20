"""Pure construction of typed persistence commands from lifecycle events."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from analysis.lifecycle import AttemptOutcomeEvent, LifecycleReducer, PlanClaimedEvent
from analysis.models import (
    AnalysisSessionState,
    ExecutionAttempt,
    ExecutionCompletion,
    ExecutionPlan,
)
from analysis.store import AttemptCompletionCommand, ClaimPlanCommand


def build_plan_claim_command(
    *,
    state: AnalysisSessionState,
    plan: ExecutionPlan,
    execution_id: str,
    token: str,
    worker_id: str,
    claimed_at: datetime,
    lease_seconds: int,
) -> ClaimPlanCommand:
    transition = LifecycleReducer.reduce(
        state,
        PlanClaimedEvent(
            plan=plan,
            execution_id=execution_id,
            token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
            worker_id=worker_id,
            claimed_at=claimed_at,
            lease_expires_at=claimed_at + timedelta(seconds=lease_seconds),
        ),
    )
    return ClaimPlanCommand(
        transition=transition,
        attempt=transition.execution_attempts[0],
        token=token,
    )


def build_attempt_completion_command(
    *,
    state: AnalysisSessionState,
    attempt: ExecutionAttempt,
    token: str,
    completion: ExecutionCompletion,
    completed_at: datetime,
) -> AttemptCompletionCommand:
    transition = LifecycleReducer.reduce(
        state,
        AttemptOutcomeEvent(
            attempt=attempt,
            completion=completion,
            completed_at=completed_at,
        ),
    )
    return AttemptCompletionCommand(
        transition=transition,
        execution_id=attempt.execution_id,
        token=token,
    )
