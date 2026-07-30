"""Tests for policyengine.py catalogue evidence in the opening-turn gateway."""

from types import SimpleNamespace
from unittest.mock import patch

from gateway.catalogue import (
    CatalogueEvidence,
    CatalogueMatch,
    CatalogueQuery,
    resolve_catalogue_queries,
)
from gateway.runtime import (
    GatewayVerdict,
    apply_catalogue_evidence,
    gateway_writer_directive,
    serialise_plan_for_system,
)


def _match(kind="reform_target", query="capital gains tax"):
    return CatalogueMatch(
        kind=kind,
        query=query,
        identifier="gov.hmrc.cgt.basic_rate" if kind == "reform_target" else "capital_gains_tax",
        label="Capital gains tax basic rate" if kind == "reform_target" else "Capital gains tax",
    )


def _evidence(*matches, unresolved_queries=(), available=True):
    return CatalogueEvidence(
        available=available,
        matches=tuple(matches),
        unresolved_queries=tuple(unresolved_queries),
    )


def test_resolver_searches_live_parameter_and_variable_catalogues():
    queries = [
        CatalogueQuery("reform_target", "capital gains tax"),
        CatalogueQuery("variable", "capital gains tax"),
    ]
    with (
        patch("gateway.catalogue.uk_model_version", return_value=object()),
        patch(
            "gateway.catalogue.search_reform_targets",
            return_value=[
                {
                    "path": "gov.hmrc.cgt.basic_rate",
                    "label": "Capital gains tax basic rate",
                }
            ],
        ) as reform_search,
        patch(
            "gateway.catalogue.search_variables",
            return_value={
                "variables": [
                    {"name": "capital_gains_tax", "label": "Capital gains tax"}
                ]
            },
        ) as variable_search,
    ):
        evidence = resolve_catalogue_queries(queries)

    assert evidence.available
    assert {match.identifier for match in evidence.matches} == {
        "gov.hmrc.cgt.basic_rate",
        "capital_gains_tax",
    }
    assert not evidence.unresolved_queries
    reform_search.assert_called_once_with("capital gains tax", limit=5)
    variable_search.assert_called_once_with("capital gains tax", limit=5)


def test_matching_catalogue_evidence_prevents_a_false_refusal():
    verdict = GatewayVerdict(outcome="out_of_scope", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "ready"
    assert resolved.route == "compute"
    assert resolved.catalogue_evidence.matches == (_match(),)


def test_matching_catalogue_evidence_preserves_other_ambiguity():
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_slots=["reform"],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "needs_plan"
    assert resolved.route == "lightweight"
    assert resolved.gating_slots == ["reform"]


def test_matching_catalogue_evidence_preserves_partial_outcome():
    verdict = GatewayVerdict(
        outcome="partial",
        route="lightweight",
        unmodellable_outputs=["inflation"],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "partial"
    assert resolved.route == "lightweight"


def test_unresolved_catalogue_query_asks_for_clarification():
    query = CatalogueQuery("reform_target", "made up levy")
    verdict = GatewayVerdict(outcome="ready", route="compute")

    resolved = apply_catalogue_evidence(
        verdict,
        _evidence(unresolved_queries=(query,)),
    )

    assert resolved.outcome == "needs_plan"
    assert resolved.route == "lightweight"
    assert "model_catalogue" in resolved.gating_slots
    directive = gateway_writer_directive(resolved)
    assert "made up levy" in directive
    assert "not say that it is unmodelled" in directive


def test_unavailable_catalogue_fails_open_to_compute():
    verdict = GatewayVerdict(outcome="out_of_scope", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "ready"
    assert resolved.route == "compute"


def test_compute_context_gets_paths_but_lightweight_context_gets_labels_only():
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        catalogue_evidence=_evidence(_match()),
    )
    lightweight = gateway_writer_directive(verdict)
    assert "Capital gains tax basic rate" in lightweight
    assert "gov.hmrc.cgt.basic_rate" not in lightweight

    compute = serialise_plan_for_system(
        GatewayVerdict(
            outcome="ready",
            route="compute",
            catalogue_evidence=_evidence(_match()),
        )
    )
    assert "Capital gains tax basic rate" in compute
    assert "gov.hmrc.cgt.basic_rate" in compute


def test_gateway_wiring_uses_matching_evidence_before_refusing():
    from gateway import runtime as gateway

    plan = {
        "in_domain": True,
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {"kind": "reform_target", "query": "capital gains tax"}
        ],
    }
    block = SimpleNamespace(type="tool_use", name="emit_plan", input=plan)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(content=[block]))
    )
    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(gateway, "resolve_catalogue_queries", return_value=_evidence(_match())),
    ):
        verdict = gateway.run_gateway(
            "Raise the lowest capital gains tax rate from 18% to 20% by income decile."
        )

    assert verdict.outcome == "ready"
    assert verdict.route == "compute"


def test_resolver_marks_a_catalogue_failure_unavailable():
    with patch("gateway.catalogue.uk_model_version", side_effect=RuntimeError("catalogue unavailable")):
        evidence = resolve_catalogue_queries(
            [CatalogueQuery("reform_target", "capital gains tax")]
        )

    assert not evidence.available
