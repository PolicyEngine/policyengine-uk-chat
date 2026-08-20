from __future__ import annotations

import pytest

from analysis.binding import BindingServices, ReformTargetSelection
from analysis.catalogue import CatalogueCandidate, CatalogueResolution
from analysis.common import AnalysisError, AnalysisErrorCode
from analysis.models import (
    AnalysisSessionState,
    EvidenceReference,
    RevisionRelationship,
    SetExactReform,
    ValidatedCandidateAnalysis,
    ValidatedCandidateField,
    ValidatedReviseAnalysis,
    ValidatedStartAnalysis,
)
from analysis.request_compiler import (
    CompilationClarification,
    CompilationInput,
    CompiledRequest,
    RequestCompilationFailed,
    RequestCompiler,
    RequestUnsupported,
)
from analysis_helpers import NOW, VERSIONS, revision


def _evidence(quote: str) -> EvidenceReference:
    return EvidenceReference(quote=quote, message_sha256="message")


def _field(value, quote: str | None = None) -> ValidatedCandidateField:
    return ValidatedCandidateField(
        value=value,
        evidence=_evidence(quote or str(value)),
    )


def _start(
    kind: str = "society",
    *,
    fields: dict | None = None,
    outputs: tuple[str, ...] = ("budgetary_impact",),
) -> ValidatedStartAnalysis:
    return ValidatedStartAnalysis(
        candidate=ValidatedCandidateAnalysis(
            analysis_kind=_field(kind),
            fields={name: _field(value) for name, value in (fields or {}).items()},
            outputs=outputs,
        )
    )


def _state(*, current=None) -> AnalysisSessionState:
    return AnalysisSessionState(
        session_id="session_test",
        state_version=1,
        phase="completed" if current else "idle",
        active_revision_id=current.revision_id if current else None,
        updated_at=NOW,
    )


def _catalogue(kind: str, query: str) -> CatalogueResolution:
    identifier = (
        "gov.hmrc.income_tax.rates.uk[0].rate"
        if kind == "reform_target"
        else "household_net_income"
    )
    return CatalogueResolution(
        available=True,
        query=query,
        candidates=(
            CatalogueCandidate(
                kind=kind,
                query=query,
                identifier=identifier,
                label=query,
                match_type="exact_label",
                score=1,
            ),
        ),
    )


def _services(**changes) -> BindingServices:
    values = {
        "catalogue_resolver": _catalogue,
        "reform_validator": lambda reform, _year: {
            "valid": True,
            "normalized_reform": reform,
        },
        "current_value_resolver": lambda paths, _year: {
            path: 0.2 for path in paths
        },
        "default_year": 2026,
        **changes,
    }
    return BindingServices(**values)


def _input(update, *, current=None) -> CompilationInput:
    return CompilationInput(
        update=update,
        state=_state(current=current),
        current_revision=current,
        active_clarification=None,
        turn_id="turn_next",
        runtime_versions=VERSIONS,
        created_at=NOW,
    )


def _compiler(services=None) -> RequestCompiler:
    return RequestCompiler(binding_services=services or _services())


def test_compiler_returns_complete_compiled_decision():
    decision = _compiler().compile(_input(_start()))

    assert isinstance(decision, CompiledRequest)
    assert decision.revision.turn_id == "turn_next"
    assert decision.bound_request.request_revision_id == decision.revision.revision_id
    assert tuple(step.operation for step in decision.plan.steps) == (
        "run_society_simulation",
        "compute_budgetary_impact",
    )


def test_compiler_returns_clarification_decision():
    decision = _compiler().compile(_input(_start(outputs=())))

    assert isinstance(decision, CompilationClarification)
    assert decision.clarification.target_field == "outputs"


def test_compiler_returns_unsupported_decision():
    decision = _compiler().compile(
        _input(_start(fields={"jurisdiction": "us"}))
    )

    assert isinstance(decision, RequestUnsupported)
    assert "only UK" in decision.reason


def test_stale_revision_fails_before_creating_another_revision():
    current = revision()
    update = ValidatedReviseAnalysis(
        base_revision_id="rev_stale",
        relationship=RevisionRelationship.CORRECTION,
    )

    decision = _compiler().compile(_input(update, current=current))

    assert isinstance(decision, RequestCompilationFailed)
    assert decision.revision is None
    assert decision.error_code == AnalysisErrorCode.STATE_PRECONDITION_FAILED


def test_same_input_and_versions_produce_same_bound_request_and_plan_hash():
    compilation_input = _input(_start())

    first = _compiler().compile(compilation_input)
    second = _compiler().compile(compilation_input)

    assert isinstance(first, CompiledRequest)
    assert isinstance(second, CompiledRequest)
    assert first.bound_request.bound_request_id == second.bound_request.bound_request_id
    assert first.plan.plan_hash == second.plan.plan_hash


def test_explicit_false_reform_value_survives_reduction_and_binding():
    decision = _compiler().compile(
        _input(
            _start(
                fields={
                    "reform_intent": "income tax rate",
                    "reform_instruction": SetExactReform(value=False),
                }
            )
        )
    )

    assert isinstance(decision, CompiledRequest)
    value = decision.bound_request.fields["reform"].value[
        "gov.hmrc.income_tax.rates.uk[0].rate"
    ]
    assert value is False


def test_expected_binding_dependency_failure_is_typed():
    def unavailable(_kind: str, _query: str) -> CatalogueResolution:
        raise AnalysisError(
            AnalysisErrorCode.BINDING_FAILED,
            "authoritative catalogue is unavailable",
        )

    decision = _compiler(_services(catalogue_resolver=unavailable)).compile(
        _input(
            _start(
                "parameter_lookup",
                fields={"parameter_query": "income tax"},
                outputs=("parameter_lookup",),
            ),
        )
    )

    assert isinstance(decision, RequestCompilationFailed)
    assert decision.error_code == AnalysisErrorCode.BINDING_FAILED


def test_target_selection_can_only_choose_from_authoritative_candidates():
    candidates = (
        CatalogueCandidate(
            kind="reform_target",
            query="income tax",
            identifier="parameter.first",
            label="First parameter",
            match_type="keyword",
            score=0.9,
        ),
        CatalogueCandidate(
            kind="reform_target",
            query="income tax",
            identifier="parameter.second",
            label="Second parameter",
            match_type="keyword",
            score=0.9,
        ),
    )

    def ambiguous(kind: str, query: str) -> CatalogueResolution:
        return CatalogueResolution(
            available=True,
            query=query,
            candidates=candidates,
        )

    def select(request):
        assert request.candidates == candidates
        return ReformTargetSelection(bindings=(request.candidates[1],))

    services = _services(
        catalogue_resolver=ambiguous,
        reform_target_selector=select,
    )
    decision = _compiler(services).compile(
        _input(
            _start(
                fields={
                    "reform_intent": "income tax",
                    "reform_instruction": SetExactReform(value=0.25),
                }
            ),
        )
    )

    assert isinstance(decision, CompiledRequest)
    reform = decision.bound_request.fields["reform"].value
    assert reform == {"parameter.second": 0.25}


def test_unexpected_binding_programming_error_is_not_relabelled():
    def broken(_kind: str, _query: str) -> CatalogueResolution:
        raise RuntimeError("broken resolver implementation")

    with pytest.raises(RuntimeError, match="broken resolver implementation"):
        _compiler(_services(catalogue_resolver=broken)).compile(
            _input(
                _start(
                    "parameter_lookup",
                    fields={"parameter_query": "income tax"},
                    outputs=("parameter_lookup",),
                ),
            )
        )
