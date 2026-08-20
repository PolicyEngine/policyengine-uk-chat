from __future__ import annotations

import pytest

from analysis.binding import (
    BindingFailed,
    BindingServices,
    NeedsClarification,
    Ready,
    RequestBinder,
    Unsupported,
)
from analysis.capabilities import CAPABILITY_REGISTRY
from analysis.catalogue import CatalogueCandidate, CatalogueResolution
from analysis.models import (
    AbolishReform,
    ChangeReformByAmount,
    ChangeReformByPercent,
    DirectionOnlyReform,
    SetExactReform,
)
from analysis_helpers import VERSIONS, revision


def _catalogue(_kind, query):
    identifier = (
        "gov.hmrc.income_tax.rates.uk[0].rate"
        if "rate" in query or "tax" in query
        else "household_net_income"
    )
    return CatalogueResolution(
        available=True,
        query=query,
        candidates=(
            CatalogueCandidate(
                kind="reform_target" if _kind == "reform_target" else "variable",
                query=query,
                identifier=identifier,
                label=query.title(),
                match_type="exact_label",
                score=1,
            ),
        ),
    )


def _validator(reform, _year):
    return {"valid": True, "normalized_reform": reform}


def _bind(kind, *, fields=None, outputs=()):
    binder = RequestBinder(
        services=BindingServices(
            default_year=2026,
            catalogue_resolver=_catalogue,
            reform_validator=_validator,
            current_value_resolver=lambda paths, _year: {
                path: 0.2 for path in paths
            },
            inactive_value_resolver=lambda paths, _year: {
                path: False for path in paths
            },
        )
    )
    return binder.bind(
        revision(kind, fields=fields, outputs=outputs),
        runtime_versions=VERSIONS,
    )


def test_binding_returns_immutable_bound_request_without_changing_semantics():
    semantic = revision("society", outputs=("budgetary_impact",))
    before = semantic.model_dump_json()
    decision = RequestBinder(
        services=BindingServices(default_year=2026)
    ).bind(semantic, runtime_versions=VERSIONS)
    assert isinstance(decision, Ready)
    assert semantic.model_dump_json() == before
    assert decision.bound_request.fields["year"].provenance.value == "default"
    assert decision.bound_request.capability_version == CAPABILITY_REGISTRY.version


@pytest.mark.parametrize(
    ("instruction", "expected"),
    [
        (SetExactReform(value=False), False),
        (SetExactReform(value=0), 0),
        (ChangeReformByAmount(amount=0.1), pytest.approx(0.3)),
        (ChangeReformByPercent(percent=50), pytest.approx(0.3)),
        (AbolishReform(), False),
    ],
)
def test_typed_reform_instructions_bind_deterministically(instruction, expected):
    decision = _bind(
        "society",
        fields={
            "reform_intent": "income tax rate",
            "reform_instruction": instruction,
        },
        outputs=("budgetary_impact",),
    )
    assert isinstance(decision, Ready)
    reform = decision.bound_request.fields["reform"].value
    value = reform["gov.hmrc.income_tax.rates.uk[0].rate"]
    if expected is False:
        assert value is False
    elif expected == 0:
        assert type(value) is int and value == 0
    else:
        assert value == expected


def test_direction_only_reform_requires_clarification():
    decision = _bind(
        "society",
        fields={
            "reform_intent": "income tax rate",
            "reform_instruction": DirectionOnlyReform(direction="increase"),
        },
        outputs=("budgetary_impact",),
    )
    assert isinstance(decision, NeedsClarification)
    assert decision.clarification.target_field == "reform_instruction"


def test_missing_reform_for_validation_is_not_ready():
    decision = _bind("reform_validation", outputs=("reform_validity",))
    assert isinstance(decision, NeedsClarification)


def test_parameter_lookup_uses_unique_highest_confidence_catalogue_match():
    def resolver(_kind, query):
        return CatalogueResolution(
            available=True,
            query=query,
            candidates=(
                CatalogueCandidate(
                    kind="reform_target",
                    query=query,
                    identifier="income_tax_personal_allowance",
                    label="Personal allowance",
                    match_type="exact_alias",
                    score=1.0,
                ),
                CatalogueCandidate(
                    kind="reform_target",
                    query=query,
                    identifier="housing_benefit_personal_allowance",
                    label="Housing benefit personal allowance",
                    match_type="strong_phrase",
                    score=0.9,
                ),
            ),
        )

    decision = RequestBinder(
        services=BindingServices(
            default_year=2026,
            catalogue_resolver=resolver,
        )
    ).bind(
        revision(
            "parameter_lookup",
            fields={"parameter_query": "personal allowance"},
            outputs=(),
        ),
        runtime_versions=VERSIONS,
    )

    assert isinstance(decision, Ready)
    assert (
        decision.bound_request.fields["parameter_path"].value
        == "income_tax_personal_allowance"
    )


def test_parameter_lookup_clarifies_tied_authoritative_catalogue_matches():
    def resolver(_kind, query):
        return CatalogueResolution(
            available=True,
            query=query,
            candidates=tuple(
                CatalogueCandidate(
                    kind="reform_target",
                    query=query,
                    identifier=f"parameter_{index}",
                    label=f"Parameter {index}",
                    match_type="strong_phrase",
                    score=0.9,
                )
                for index in range(2)
            ),
        )

    decision = RequestBinder(
        services=BindingServices(
            default_year=2026,
            catalogue_resolver=resolver,
        )
    ).bind(
        revision(
            "parameter_lookup",
            fields={"parameter_query": "shared phrase"},
            outputs=(),
        ),
        runtime_versions=VERSIONS,
    )

    assert isinstance(decision, NeedsClarification)
    assert decision.clarification.target_field == "parameter_query"


def test_reform_uses_unique_highest_confidence_catalogue_match():
    def resolver(_kind, query):
        return CatalogueResolution(
            available=True,
            query=query,
            candidates=(
                CatalogueCandidate(
                    kind="reform_target",
                    query=query,
                    identifier="income_tax_personal_allowance",
                    label="Personal allowance",
                    match_type="exact_alias",
                    score=1.0,
                ),
                CatalogueCandidate(
                    kind="reform_target",
                    query=query,
                    identifier="housing_benefit_personal_allowance",
                    label="Housing benefit personal allowance",
                    match_type="strong_phrase",
                    score=0.9,
                ),
            ),
        )

    def unexpected_selection(_request):
        raise AssertionError("a unique highest-confidence match needs no selection")

    decision = RequestBinder(
        services=BindingServices(
            default_year=2026,
            catalogue_resolver=resolver,
            reform_target_selector=unexpected_selection,
            reform_validator=_validator,
        )
    ).bind(
        revision(
            "reform_validation",
            fields={
                "reform_intent": "personal allowance",
                "reform_instruction": SetExactReform(value=15000),
            },
            outputs=(),
        ),
        runtime_versions=VERSIONS,
    )

    assert isinstance(decision, Ready)
    assert decision.bound_request.fields["reform"].value == {
        "income_tax_personal_allowance": 15000
    }


def test_missing_society_output_is_not_ready():
    assert isinstance(_bind("society"), NeedsClarification)


def test_unsupported_jurisdiction_is_typed_unsupported():
    decision = _bind(
        "society",
        fields={"jurisdiction": "us"},
        outputs=("budgetary_impact",),
    )
    assert isinstance(decision, Unsupported)


def test_invalid_deterministic_reform_is_binding_failure():
    decision = RequestBinder(
        services=BindingServices(
            default_year=2026,
            catalogue_resolver=_catalogue,
            reform_validator=lambda *_: {"valid": False},
            current_value_resolver=lambda paths, _year: {
                path: 0.2 for path in paths
            },
        )
    ).bind(
        revision(
            "society",
            fields={
                "reform_intent": "income tax rate",
                "reform_instruction": ChangeReformByAmount(amount=1),
            },
            outputs=("budgetary_impact",),
        ),
        runtime_versions=VERSIONS,
    )
    assert isinstance(decision, BindingFailed)


def test_invalid_authoritative_household_is_not_ready():
    decision = RequestBinder(
        services=BindingServices(
            default_year=2026,
            household_validator=lambda **_kwargs: {
                "valid": False,
                "errors": [{"message": "age must be non-negative"}],
            },
        )
    ).bind(
        revision("household", fields={"people": [{"age": -1}]}),
        runtime_versions=VERSIONS,
    )
    assert isinstance(decision, NeedsClarification)
    assert decision.clarification.target_field == "people"
    assert "age must be non-negative" in decision.clarification.prompt


@pytest.mark.parametrize(
    ("kind", "output", "fields"),
    [
        ("parameter_lookup", "parameter_lookup", {"parameter_query": "income tax rate"}),
        ("reform_validation", "reform_validity", {"reform": {"p": 1}}),
        ("household", "net_income", {"people": [{"age": 30}]}),
        ("household", "benefit_entitlement", {"people": [{"age": 30}]}),
        ("society", "budgetary_impact", {}),
        ("society", "poverty_impact", {}),
        ("society", "inequality_impact", {}),
        ("society", "decile_impact", {}),
        ("society", "winners_losers", {}),
        ("society", "program_breakdown", {}),
        ("society", "aggregate", {"variable_query": "net income", "aggregate_entity": "household", "aggregate_operation": "sum"}),
        ("society", "caseload", {"variable_query": "net income", "aggregate_entity": "household"}),
        ("society", "marginal_rate", {"variable_query": "net income", "aggregate_entity": "household"}),
        ("society", "chart", {"chart_kind": "budget_waterfall"}),
        ("exploratory", "budgetary_impact", {"objective": "trace fiscal effects"}),
    ],
)
def test_registered_kind_output_combinations_reach_ready(kind, output, fields):
    decision = _bind(kind, fields=fields, outputs=(output,))
    assert isinstance(decision, Ready), decision
    assert output in decision.bound_request.outputs
    assert decision.bound_request.output_producers


def test_multi_output_binding_deduplicates_shared_producer():
    decision = _bind(
        "society",
        outputs=("budgetary_impact", "tax_revenue", "benefit_spending"),
    )
    assert isinstance(decision, Ready)
    assert decision.bound_request.output_producers.count(
        "producer:budgetary_impact"
    ) == 1
