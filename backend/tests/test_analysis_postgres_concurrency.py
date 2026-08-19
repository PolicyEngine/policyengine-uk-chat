"""PostgreSQL transaction interleavings for the analysis lifecycle.

Set ANALYSIS_TEST_POSTGRES_URL to run these tests. The continuous-integration
backend job supplies a dedicated PostgreSQL 16 service.
"""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from analysis.common import AnalysisError
from analysis.compiler import compile_plan
from analysis.lifecycle import LifecycleReducer, PlanReadyEvent
from analysis.models import ExecutionCompletion
from analysis.persistence import AnalysisStateStore
from analysis_helpers import NOW, bound_request, revision


POSTGRES_URL = os.environ.get("ANALYSIS_TEST_POSTGRES_URL")
pytestmark = pytest.mark.skipif(
    not POSTGRES_URL,
    reason="ANALYSIS_TEST_POSTGRES_URL is not configured",
)


@pytest.fixture(scope="module")
def postgres_store():
    import psycopg2

    assert POSTGRES_URL is not None
    migration = Path(
        "supabase/migrations/006_analysis_compiler_hardening.sql"
    ).read_text()
    connection = psycopg2.connect(POSTGRES_URL)
    try:
        connection.autocommit = True
        with connection.cursor() as cursor:
            cursor.execute(migration)
            cursor.execute(migration)
    finally:
        connection.close()
    engine = create_engine(POSTGRES_URL, pool_pre_ping=True)
    try:
        yield AnalysisStateStore(engine)
    finally:
        engine.dispose()


def _ready(store: AnalysisStateStore, suffix: str):
    suffix = f"{suffix}_{uuid4().hex}"
    session_id = f"postgres_{suffix}"
    state = store.create_session(session_id, at=NOW)
    semantic = revision(session_id=session_id, revision_id=f"rev_{suffix}")
    bound = bound_request(
        session_id=session_id,
        revision_id=semantic.revision_id,
    )
    plan = compile_plan(bound)
    ready = store.commit_transition(
        LifecycleReducer.reduce(
            state,
            PlanReadyEvent(
                revision=semantic,
                bound_request=bound,
                plan=plan,
            ),
        )
    )
    return ready, semantic, bound, plan


def _race(*operations):
    barrier = Barrier(len(operations))

    def run(operation):
        barrier.wait()
        try:
            return operation()
        except AnalysisError as exc:
            return exc

    with ThreadPoolExecutor(max_workers=len(operations)) as pool:
        return list(pool.map(run, operations))


def _claim(store, ready, plan, worker="worker"):
    return store.claim_plan(
        session_id=str(ready.session_id),
        plan=plan,
        worker_id=worker,
        expected_state_version=ready.state_version,
    )


def test_postgres_claim_versus_claim(postgres_store):
    ready, _semantic, _bound, plan = _ready(postgres_store, "claim_claim")
    results = _race(
        lambda: _claim(postgres_store, ready, plan, "one"),
        lambda: _claim(postgres_store, ready, plan, "two"),
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1


def test_postgres_claim_versus_cancel(postgres_store):
    from analysis.lifecycle import CancellationRequestedEvent

    ready, _semantic, _bound, plan = _ready(postgres_store, "claim_cancel")
    cancellation = LifecycleReducer.reduce(
        ready,
        CancellationRequestedEvent(request_revision_id=ready.active_revision_id),
    )
    results = _race(
        lambda: _claim(postgres_store, ready, plan),
        lambda: postgres_store.commit_transition(cancellation),
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1
    state = postgres_store.load_state(str(ready.session_id))
    assert state.phase.value in {"executing", "cancelled"}


def test_postgres_revision_versus_completion(postgres_store):
    ready, _semantic, _bound, plan = _ready(
        postgres_store,
        "revision_completion",
    )
    claim = _claim(postgres_store, ready, plan)
    replacement_revision = revision(
        session_id=str(ready.session_id),
        revision_id=f"rev_{ready.session_id}_new",
        revision_number=2,
        turn_id="turn_revision_completion_new",
        outputs=("poverty_impact",),
    )
    replacement_bound = bound_request(
        session_id=str(ready.session_id),
        revision_id=replacement_revision.revision_id,
        turn_id=replacement_revision.turn_id,
        outputs=("poverty_impact",),
    )
    replacement_plan = compile_plan(replacement_bound)
    replacement = LifecycleReducer.reduce(
        claim.state,
        PlanReadyEvent(
            revision=replacement_revision,
            bound_request=replacement_bound,
            plan=replacement_plan,
        ),
    )
    results = _race(
        lambda: postgres_store.commit_transition(replacement),
        lambda: postgres_store.finish_attempt(
            state=claim.state,
            attempt=claim.attempt,
            token=claim.token,
            completion=ExecutionCompletion(
                execution_id=claim.attempt.execution_id,
                status="completed",
            ),
        ),
    )
    assert sum(not isinstance(result, Exception) for result in results) == 1


def test_postgres_recovery_versus_completion(postgres_store):
    ready, _semantic, _bound, plan = _ready(
        postgres_store,
        "recovery_completion",
    )
    claim = _claim(postgres_store, ready, plan)
    results = _race(
        lambda: postgres_store.recover_expired_attempts(
            at=claim.attempt.lease_expires_at + timedelta(seconds=1),
        ),
        lambda: postgres_store.finish_attempt(
            state=claim.state,
            attempt=claim.attempt,
            token=claim.token,
            completion=ExecutionCompletion(
                execution_id=claim.attempt.execution_id,
                status="completed",
            ),
        ),
    )
    recovery_closed_attempt = results[0] == (claim.attempt.execution_id,)
    completion_closed_attempt = not isinstance(results[1], Exception)
    assert recovery_closed_attempt is not completion_closed_attempt
    assert postgres_store.load_attempt(claim.attempt.execution_id).status.value in {
        "completed",
        "expired",
    }


def test_postgres_pending_plan_promotion_and_claim(postgres_store):
    ready, _semantic, _bound, plan = _ready(postgres_store, "promotion_claim")
    claim = _claim(postgres_store, ready, plan)
    replacement_revision = revision(
        session_id=str(ready.session_id),
        revision_id=f"rev_{ready.session_id}_new",
        revision_number=2,
        turn_id="turn_promotion_claim_new",
        outputs=("poverty_impact",),
    )
    replacement_bound = bound_request(
        session_id=str(ready.session_id),
        revision_id=replacement_revision.revision_id,
        turn_id=replacement_revision.turn_id,
        outputs=("poverty_impact",),
    )
    replacement_plan = compile_plan(replacement_bound)
    queued = postgres_store.commit_transition(
        LifecycleReducer.reduce(
            claim.state,
            PlanReadyEvent(
                revision=replacement_revision,
                bound_request=replacement_bound,
                plan=replacement_plan,
            ),
        )
    )
    _race(
        lambda: postgres_store.finish_attempt(
            state=queued,
            attempt=claim.attempt,
            token=claim.token,
            completion=ExecutionCompletion(
                execution_id=claim.attempt.execution_id,
                status="cancelled",
            ),
        ),
        lambda: _claim(postgres_store, queued, replacement_plan, "replacement"),
    )
    state = postgres_store.load_state(str(ready.session_id))
    if state.phase.value == "ready":
        state = _claim(
            postgres_store,
            state,
            replacement_plan,
            "replacement_after_promotion",
        ).state
    assert state.phase.value == "executing"
    assert state.active_plan_id == replacement_plan.plan_id
    assert state.pending_plan_id is None
