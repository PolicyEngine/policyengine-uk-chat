"""Tests for policyengine.py catalogue evidence in the opening-turn gateway."""

from types import SimpleNamespace
from unittest.mock import patch

from gateway.catalogue import (
    CANDIDATE_LIMIT,
    CatalogueEvidence,
    CatalogueMatch,
    CatalogueQuery,
    MAX_CATALOGUE_QUERIES,
    _classify_match,
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
    reform_search.assert_called_once_with(
        "capital gains tax", limit=CANDIDATE_LIMIT
    )
    variable_search.assert_called_once_with(
        "capital gains tax", limit=CANDIDATE_LIMIT
    )


def test_match_quality_separates_authority_from_suggestions():
    assert _classify_match(
        "gov.hmrc.cgt.basic_rate",
        identifier="gov.hmrc.cgt.basic_rate",
        label="Capital gains tax basic rate",
    ) == ("exact_identifier", 1.0)
    assert _classify_match(
        "lowest CGT rate",
        identifier="gov.hmrc.cgt.basic_rate",
        label="Capital gains tax basic rate",
        aliases=("lowest CGT rate",),
    ) == ("exact_alias", 1.0)
    assert _classify_match(
        "capital gains tax",
        identifier="gov.hmrc.cgt.basic_rate",
        label="Capital gains tax basic rate",
    ) == ("strong_phrase", 0.9)
    match_type, score = _classify_match(
        "US federal income tax",
        identifier="gov.hmrc.income_tax.rates.uk",
        label="UK income tax rates",
    )
    assert match_type == "fuzzy_suggestion"
    assert 0 < score < 0.9


def test_fuzzy_only_catalogue_results_remain_unresolved_suggestions():
    query = CatalogueQuery(
        "reform_target",
        "US federal income tax",
        "US federal income tax",
    )
    with (
        patch("gateway.catalogue.uk_model_version", return_value=object()),
        patch(
            "gateway.catalogue.search_reform_targets",
            return_value=[
                {
                    "path": "gov.hmrc.income_tax.rates.uk",
                    "label": "UK income tax rates",
                    "aliases": [],
                }
            ],
        ),
    ):
        evidence = resolve_catalogue_queries([query])

    assert evidence.authoritative_matches == ()
    assert len(evidence.suggestions) == 1
    assert evidence.suggestions[0].match_type == "fuzzy_suggestion"
    assert evidence.unresolved_queries == (query,)


def test_fuzzy_suggestion_cannot_promote_a_missing_tool_to_compute():
    suggestion = CatalogueMatch(
        kind="reform_target",
        query="US federal income tax",
        identifier="gov.hmrc.income_tax.rates.uk",
        label="UK income tax rates",
        match_type="fuzzy_suggestion",
        score=0.7,
    )
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        tool=None,
        gating_slots=["tool"],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(suggestion))

    assert resolved.outcome == "needs_plan"
    assert resolved.route == "lightweight"


def test_plan_schema_requires_bounded_catalogue_queries():
    from gateway.runtime import _EMIT_PLAN_TOOL

    schema = _EMIT_PLAN_TOOL["input_schema"]
    queries = schema["properties"]["catalogue_queries"]

    assert "catalogue_queries" in schema["required"]
    assert queries["maxItems"] == MAX_CATALOGUE_QUERIES
    assert queries["items"]["required"] == ["kind", "query", "evidence"]
    assert queries["items"]["properties"]["kind"]["enum"] == [
        "reform_target",
        "variable",
    ]


def test_resolver_normalises_duplicate_blank_and_excess_queries():
    queries = [
        CatalogueQuery("reform_target", " Capital gains tax "),
        CatalogueQuery("reform_target", "capital gains tax"),
        CatalogueQuery("variable", ""),
        CatalogueQuery("variable", "net income"),
        CatalogueQuery("variable", "income tax"),
        CatalogueQuery("variable", "universal credit"),
        CatalogueQuery("variable", "child benefit"),
    ]
    with (
        patch("gateway.catalogue.uk_model_version", return_value=object()),
        patch("gateway.catalogue.search_reform_targets", return_value=[] ) as reform_search,
        patch("gateway.catalogue.search_variables", return_value={"variables": []}) as variable_search,
    ):
        evidence = resolve_catalogue_queries(queries)

    assert evidence.unresolved_queries == (
        CatalogueQuery("reform_target", "Capital gains tax"),
        CatalogueQuery("variable", "net income"),
        CatalogueQuery("variable", "income tax"),
        CatalogueQuery("variable", "universal credit"),
    )
    assert reform_search.call_count == 1
    assert variable_search.call_count == 3


def test_matching_catalogue_evidence_prevents_a_false_refusal():
    verdict = GatewayVerdict(outcome="out_of_scope", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "ready"
    assert resolved.route == "compute"
    assert resolved.catalogue_evidence.matches == (_match(),)


def test_matching_catalogue_evidence_cannot_override_irrelevant():
    verdict = GatewayVerdict(outcome="irrelevant", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "irrelevant"
    assert resolved.route == "lightweight"


def test_unresolved_catalogue_evidence_cannot_override_irrelevant():
    query = CatalogueQuery("reform_target", "made up levy")
    verdict = GatewayVerdict(outcome="irrelevant", route="lightweight")

    resolved = apply_catalogue_evidence(
        verdict,
        _evidence(unresolved_queries=(query,)),
    )

    assert resolved.outcome == "irrelevant"
    assert resolved.route == "lightweight"


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


def test_unresolved_catalogue_query_preserves_partial_limitation():
    query = CatalogueQuery("reform_target", "made up levy")
    verdict = GatewayVerdict(
        outcome="partial",
        route="lightweight",
        unmodellable_outputs=["GDP"],
    )

    resolved = apply_catalogue_evidence(
        verdict,
        _evidence(unresolved_queries=(query,)),
    )

    assert resolved.outcome == "partial"
    assert resolved.route == "lightweight"
    assert "model_catalogue" in resolved.gating_slots
    directive = gateway_writer_directive(resolved)
    assert "Cannot model: GDP." in directive
    assert "made up levy" in directive
    assert "before offering to run the modellable part" in directive


def test_unavailable_catalogue_fails_open_to_compute():
    verdict = GatewayVerdict(outcome="out_of_scope", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "ready"
    assert resolved.route == "compute"


def test_unavailable_catalogue_preserves_irrelevant_outcome():
    verdict = GatewayVerdict(outcome="irrelevant", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "irrelevant"
    assert resolved.route == "lightweight"


def test_unavailable_catalogue_preserves_existing_partial_outcome():
    verdict = GatewayVerdict(
        outcome="partial",
        route="lightweight",
        unmodellable_outputs=["inflation"],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "partial"
    assert resolved.route == "lightweight"


def test_unavailable_catalogue_preserves_existing_needs_plan_outcome():
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_slots=["reform"],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "needs_plan"
    assert resolved.route == "lightweight"
    assert resolved.gating_slots == ["reform"]


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
        "domain": {"status": "uk_or_unspecified"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "capital gains tax",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "capital gains tax",
                "evidence": "capital gains tax",
            }
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


def test_gateway_drops_catalogue_queries_without_prompt_grounding():
    from gateway import runtime as gateway

    prompt = "How would US federal income tax change?"
    plan = {
        "domain": {"status": "explicit_non_uk", "evidence": "US federal"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "federal income tax",
        },
        "tool": "none",
        "slots": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "income tax",
                "evidence": "federal income tax",
            },
            {
                "kind": "variable",
                "query": "universal credit",
                "evidence": "universal credit",
            },
            {
                "kind": "variable",
                "query": "capital gains tax",
                "evidence": "US federal income tax",
            },
        ],
    }
    block = SimpleNamespace(type="tool_use", name="emit_plan", input=plan)
    client = SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **_kwargs: SimpleNamespace(content=[block])
        )
    )

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway,
            "resolve_catalogue_queries",
            return_value=_evidence(),
        ) as resolver,
    ):
        gateway.run_gateway(prompt)

    resolver.assert_called_once_with(
        (
            CatalogueQuery(
                "reform_target",
                "income tax",
                "federal income tax",
            ),
        )
    )


def test_resolver_marks_a_catalogue_failure_unavailable():
    with patch("gateway.catalogue.uk_model_version", side_effect=RuntimeError("catalogue unavailable")):
        evidence = resolve_catalogue_queries(
            [CatalogueQuery("reform_target", "capital gains tax")]
        )

    assert not evidence.available
