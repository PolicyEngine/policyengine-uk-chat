from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
import weakref

from sqlalchemy.engine import Engine

from analysis.binding import Ready, bind_request
from analysis.catalogue import CatalogueCandidate, CatalogueResolution
from analysis.common import RuntimeVersions
from analysis.compiler import compile_plan
from analysis.dependencies import Clock, TurnInterpreter
from analysis.interpreter import InterpretationResult, InterpreterContext
from analysis.models import (
    AnalysisSessionState,
    BoundRequest,
    EvidenceReference,
    ExecutionAttempt,
    ExecutionAttemptStatus,
    FieldProvenance,
    RequestField,
    SemanticRequestRevision,
)
from analysis.persistence import AnalysisStateStore
from analysis.store import AnalysisStore


NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)
VERSIONS = RuntimeVersions(
    catalogue_version="catalogue-test",
    engine_version="engine-test",
    country_package_version="country-test",
    dataset_identifier="dataset-test",
)


@dataclass(frozen=True)
class FixedClock:
    current: datetime = NOW

    def __call__(self) -> datetime:
        return self.current


@dataclass
class StaticTurnInterpreter:
    result: InterpretationResult
    calls: list[InterpreterContext] = field(default_factory=list)

    def __call__(self, context: InterpreterContext) -> InterpretationResult:
        self.calls.append(context)
        return self.result


def typed_clock(current: datetime = NOW) -> Clock:
    return FixedClock(current)


def typed_interpreter(result: InterpretationResult) -> TurnInterpreter:
    return StaticTurnInterpreter(result)


def owned_analysis_store(engine: Engine) -> AnalysisStateStore:
    """Return a test-owned store and dispose its database pool when released."""

    store = AnalysisStateStore(engine)
    weakref.finalize(store, engine.dispose)
    return store


def analysis_store_boundary(engine: Engine) -> AnalysisStore:
    return owned_analysis_store(engine)


def _catalogue(kind, query):
    return CatalogueResolution(
        available=True,
        query=query,
        candidates=(
            CatalogueCandidate(
                kind=kind,
                query=query,
                identifier=("parameter.test" if kind == "reform_target" else "variable_test"),
                label=query,
                match_type="exact_label",
                score=1,
            ),
        ),
    )


def request_field(value, provenance=FieldProvenance.USER) -> RequestField:
    return RequestField(
        value=value,
        provenance=provenance,
        evidence=EvidenceReference(quote=str(value), message_sha256="evidence"),
    )


def revision(
    kind: str = "society",
    *,
    fields: dict | None = None,
    outputs: tuple[str, ...] = ("budgetary_impact",),
    revision_id: str = "rev_test",
    revision_number: int = 1,
    turn_id: str = "turn_test",
    session_id: str = "session_test",
) -> SemanticRequestRevision:
    values = {"analysis_kind": request_field(kind)}
    values.update(
        {
            name: value if isinstance(value, RequestField) else request_field(value)
            for name, value in (fields or {}).items()
        }
    )
    return SemanticRequestRevision(
        revision_id=revision_id,
        session_id=session_id,
        revision_number=revision_number,
        turn_id=turn_id,
        relationship="new",
        fields=values,
        outputs=outputs,
        created_at=NOW,
    )


def bound_request(
    kind: str = "society",
    *,
    fields: dict | None = None,
    outputs: tuple[str, ...] = ("budgetary_impact",),
    revision_id: str = "rev_test",
    turn_id: str = "turn_test",
    session_id: str = "session_test",
) -> BoundRequest:
    decision = bind_request(
        revision(
            kind,
            fields=fields,
            outputs=outputs,
            revision_id=revision_id,
            turn_id=turn_id,
            session_id=session_id,
        ),
        runtime_versions=VERSIONS,
        default_year=2026,
        catalogue_resolver=_catalogue,
        reform_validator=lambda reform, _year: {
            "valid": True,
            "normalized_reform": reform,
        },
    )
    assert isinstance(decision, Ready), decision
    return decision.bound_request


def plan_and_records(
    kind: str = "society",
    *,
    fields: dict | None = None,
    outputs: tuple[str, ...] = ("budgetary_impact",),
):
    semantic = revision(kind, fields=fields, outputs=outputs)
    decision = bind_request(
        semantic,
        runtime_versions=VERSIONS,
        default_year=2026,
        catalogue_resolver=_catalogue,
        reform_validator=lambda reform, _year: {
            "valid": True,
            "normalized_reform": reform,
        },
    )
    assert isinstance(decision, Ready), decision
    bound = decision.bound_request
    plan = compile_plan(bound)
    state = AnalysisSessionState(
        session_id=semantic.session_id,
        state_version=2,
        phase="executing",
        active_revision_id=semantic.revision_id,
        active_bound_request_id=bound.bound_request_id,
        active_plan_id=plan.plan_id,
        active_execution_id="execution_test",
        updated_at=NOW,
    )
    attempt = ExecutionAttempt(
        execution_id="execution_test",
        session_id=semantic.session_id,
        request_revision_id=semantic.revision_id,
        bound_request_id=bound.bound_request_id,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        token_hash="not-used-by-local-verifier",
        status=ExecutionAttemptStatus.RUNNING,
        worker_id="worker_test",
        catalogue_version=plan.catalogue_version,
        engine_version=plan.engine_version,
        country_package_version=plan.country_package_version,
        dataset_identifier=plan.dataset_identifier,
        claimed_at=NOW,
        heartbeat_at=NOW,
        lease_expires_at=NOW + timedelta(minutes=3),
    )
    return semantic, bound, plan, state, attempt
