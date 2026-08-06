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
from gateway.policy import CapabilityDecision, GatingReason
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
        identifier=(
            "gov.hmrc.cgt.basic_rate"
            if kind == "reform_target"
            else "capital_gains_tax"
        ),
        label=(
            "Capital gains tax basic rate"
            if kind == "reform_target"
            else "Capital gains tax"
        ),
    )


def _evidence(*matches, unresolved_queries=(), available=True):
    return CatalogueEvidence(
        available=available,
        matches=tuple(matches),
        unresolved_queries=tuple(unresolved_queries),
    )


def _client_for_plans(*plans):
    responses = [
        SimpleNamespace(
            content=[SimpleNamespace(type="tool_use", name="emit_plan", input=plan)]
        )
        for plan in plans
    ]
    calls = []

    def create(**kwargs):
        calls.append(kwargs)
        if not responses:
            raise AssertionError("gateway requested more plans than expected")
        return responses.pop(0)

    return SimpleNamespace(
        messages=SimpleNamespace(create=create),
        calls=calls,
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
    reform_search.assert_called_once_with("capital gains tax", limit=CANDIDATE_LIMIT)
    variable_search.assert_called_once_with("capital gains tax", limit=CANDIDATE_LIMIT)


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
        gating_reasons=[GatingReason("missing_tool", "tool")],
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
        patch(
            "gateway.catalogue.search_reform_targets", return_value=[]
        ) as reform_search,
        patch(
            "gateway.catalogue.search_variables", return_value={"variables": []}
        ) as variable_search,
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


def test_matching_catalogue_evidence_does_not_directly_override_a_refusal():
    verdict = GatewayVerdict(outcome="out_of_scope", route="lightweight")

    resolved = apply_catalogue_evidence(verdict, _evidence(_match()))

    assert resolved.outcome == "out_of_scope"
    assert resolved.route == "lightweight"
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
        gating_reasons=[GatingReason("missing_reform", "reform")],
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
        gating_reasons=[GatingReason("missing_reform", "reform")],
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "needs_plan"
    assert resolved.route == "lightweight"
    assert resolved.gating_slots == ["reform"]


def test_unavailable_catalogue_fails_open_from_catalogue_uncertainty():
    verdict = GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        tool=None,
        gating_reasons=[GatingReason("missing_tool", "tool")],
        capability=CapabilityDecision(
            status="catalogue_uncertain",
            evidence="capital gains tax",
        ),
    )

    resolved = apply_catalogue_evidence(verdict, _evidence(available=False))

    assert resolved.outcome == "ready"
    assert resolved.route == "compute"
    assert resolved.gating_slots == []


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
    recovery_plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {"status": "supported"},
        "tool": "run_society_simulation",
        "slots": [
            {
                "name": "reform",
                "kind": "tool_input",
                "value": "Raise the lowest capital gains tax rate from 18% to 20%",
                "source": "prompt",
            },
            {
                "name": "output",
                "kind": "output",
                "value": "decile_impact",
                "source": "prompt",
            },
        ],
        "unmodellable_outputs": [],
        "catalogue_queries": [],
    }
    client = _client_for_plans(plan, recovery_plan)
    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway, "resolve_catalogue_queries", return_value=_evidence(_match())
        ),
    ):
        verdict = gateway.run_gateway(
            "Raise the lowest capital gains tax rate from 18% to 20% by income decile."
        )

    assert verdict.outcome == "ready"
    assert verdict.route == "compute"
    assert verdict.tool == "run_society_simulation"
    assert len(client.calls) == 2
    assert "SERVER-VERIFIED CATALOGUE CANDIDATES" in client.calls[1]["system"]
    assert "Capital gains tax basic rate" in client.calls[1]["system"]


def test_catalogue_recovery_preserves_load_bearing_ambiguity():
    from gateway import runtime as gateway

    initial_plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "support levy",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "support levy",
                "evidence": "support levy",
            }
        ],
    }
    recovery_plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {"status": "supported"},
        "tool": "run_society_simulation",
        "slots": [
            {"name": "reform", "kind": "tool_input", "source": "assumed"},
            {"name": "output", "kind": "output", "source": "assumed"},
        ],
        "unmodellable_outputs": [],
        "catalogue_queries": [],
    }
    client = _client_for_plans(initial_plan, recovery_plan)
    evidence = _evidence(_match(query="support levy"))

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(gateway, "resolve_catalogue_queries", return_value=evidence),
    ):
        verdict = gateway.run_gateway("Model the support levy.")

    assert verdict.outcome == "needs_plan"
    assert verdict.route == "lightweight"
    assert set(verdict.gating_slots) == {"reform", "output"}
    assert len(client.calls) == 2


def test_fuzzy_only_catalogue_evidence_does_not_trigger_recovery():
    from gateway import runtime as gateway

    prompt = "How would US federal income tax change?"
    query = CatalogueQuery(
        "reform_target",
        "US federal income tax",
        "US federal income tax",
    )
    initial_plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "US federal income tax",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "US federal income tax",
                "evidence": "US federal income tax",
            }
        ],
    }
    suggestion = CatalogueMatch(
        kind="reform_target",
        query=query.query,
        identifier="gov.hmrc.income_tax.rates.uk",
        label="UK income tax rates",
        match_type="fuzzy_suggestion",
        score=0.7,
    )
    client = _client_for_plans(initial_plan)

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway,
            "resolve_catalogue_queries",
            return_value=_evidence(suggestion, unresolved_queries=(query,)),
        ),
    ):
        verdict = gateway.run_gateway(prompt)

    assert verdict.outcome == "needs_plan"
    assert "model_catalogue" in verdict.gating_slots
    assert len(client.calls) == 1


def test_explicit_non_uk_request_is_terminal_even_with_authoritative_match():
    from gateway import runtime as gateway

    prompt = "How would US federal income tax change?"
    initial_plan = {
        "domain": {"status": "explicit_non_uk", "evidence": "US federal"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "federal income tax",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "income tax",
                "evidence": "federal income tax",
            }
        ],
    }
    client = _client_for_plans(initial_plan)

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway, "resolve_catalogue_queries", return_value=_evidence(_match())
        ),
    ):
        verdict = gateway.run_gateway(prompt)

    assert verdict.outcome == "irrelevant"
    assert verdict.route == "lightweight"
    assert len(client.calls) == 1


def test_catalogue_recovery_can_confirm_a_grounded_non_uk_refusal():
    from gateway import runtime as gateway

    prompt = "How would US federal income tax change?"
    initial_plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "US federal income tax",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [
            {
                "kind": "reform_target",
                "query": "income tax",
                "evidence": "US federal income tax",
            }
        ],
    }
    recovery_plan = {
        "domain": {"status": "explicit_non_uk", "evidence": "US federal"},
        "capability": {
            "status": "catalogue_uncertain",
            "evidence": "US federal income tax",
        },
        "tool": "none",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [],
    }
    client = _client_for_plans(initial_plan, recovery_plan)

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway, "resolve_catalogue_queries", return_value=_evidence(_match())
        ),
    ):
        verdict = gateway.run_gateway(prompt)

    assert verdict.outcome == "irrelevant"
    assert verdict.route == "lightweight"
    assert verdict.domain.evidence == "US federal"
    assert len(client.calls) == 2


def test_inconclusive_catalogue_recovery_fails_open_after_one_retry():
    from gateway import runtime as gateway

    initial_plan = {
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
    recovery_plan = {
        **initial_plan,
        "catalogue_queries": [],
    }
    client = _client_for_plans(initial_plan, recovery_plan)

    with (
        patch.object(gateway, "get_sync_client", lambda: client),
        patch.object(
            gateway, "resolve_catalogue_queries", return_value=_evidence(_match())
        ),
    ):
        verdict = gateway.run_gateway(
            "Raise the lowest capital gains tax rate from 18% to 20%."
        )

    assert verdict.outcome == "ready"
    assert verdict.route == "compute"
    assert verdict.tool is None
    assert verdict.gating_slots == []
    assert len(client.calls) == 2


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
    with patch(
        "gateway.catalogue.uk_model_version",
        side_effect=RuntimeError("catalogue unavailable"),
    ):
        evidence = resolve_catalogue_queries(
            [CatalogueQuery("reform_target", "capital gains tax")]
        )

    assert not evidence.available
