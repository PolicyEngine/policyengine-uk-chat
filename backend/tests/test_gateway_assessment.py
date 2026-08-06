from types import SimpleNamespace
from unittest.mock import patch

import pytest

from gateway.assessment import (
    AUTO_EXECUTE_REFORM_CONFIDENCE,
    GatewayCatalogueUnavailable,
    ReformAssessmentError,
    assess_reform_with_catalogue,
)
from gateway.intent import ReformIntent


PATH = "gov.hmrc.income_tax.allowances.personal_allowance.amount"
LABEL = "Personal allowance"


def _tool(name, tool_id, tool_input):
    return SimpleNamespace(type="tool_use", name=name, id=tool_id, input=tool_input)


def _client(*responses):
    remaining = list(responses)

    def create(**_kwargs):
        return SimpleNamespace(content=remaining.pop(0))

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def _search(_query, _limit):
    return [
        {
            "path": PATH,
            "label": LABEL,
            "description": "The amount of income exempt from income tax.",
            "unit": "currency-GBP",
            "year": 2026,
            "value": 12_570,
        }
    ]


def _valid(reform, year):
    return {
        "valid": True,
        "normalized_reform": reform,
        "parameter_paths": list(reform),
    }


def _intent():
    return ReformIntent(
        policy_phrase="personal allowance",
        action="increase",
        amount="£500",
        scope="unspecified",
        evidence="increasing the personal allowance by £500",
    )


def _assessment(confidence=80, path=PATH, label=LABEL):
    return {
        "summary": f"Increase {label} by £500",
        "confidence": confidence,
        "reform": {path: 13_070},
        "bindings": [{"parameter_path": path, "label": label}],
        "alternatives": [],
    }


def test_confidence_threshold_is_inclusive():
    assert AUTO_EXECUTE_REFORM_CONFIDENCE == 80


@pytest.mark.parametrize("confidence", [0, 79, 80, 100])
def test_resolver_constructs_and_scores_only_after_search(confidence):
    client = _client(
        [_tool("search_reform_targets", "search-1", {"query": "personal allowance"})],
        [_tool("emit_reform_assessment", "assessment-1", _assessment(confidence))],
    )

    result = assess_reform_with_catalogue(
        "What is the cost of increasing the personal allowance by £500?",
        _intent(),
        client=client,
        search=_search,
        validate=_valid,
        catalogue_version="test-version",
    )

    assert result.confidence == confidence
    assert result.reform == {PATH: 13_070}
    assert result.parameter_bindings[0].label == LABEL
    assert result.search_queries == ("personal allowance",)
    assert result.catalogue_version == "test-version"


def test_model_cannot_construct_with_an_unreturned_parameter_path():
    client = _client(
        [_tool("search_reform_targets", "search-1", {"query": "personal allowance"})],
        [_tool("emit_reform_assessment", "bad-1", _assessment(path="invented.path"))],
        [_tool("emit_reform_assessment", "bad-2", _assessment(path="invented.path"))],
    )

    with pytest.raises(ReformAssessmentError, match="search results"):
        assess_reform_with_catalogue(
            "Increase the personal allowance by £500",
            _intent(),
            client=client,
            search=_search,
            validate=_valid,
            catalogue_version="test-version",
        )


def test_model_must_use_the_catalogue_label():
    client = _client(
        [_tool("search_reform_targets", "search-1", {"query": "personal allowance"})],
        [_tool("emit_reform_assessment", "bad-1", _assessment(label="Made up label"))],
        [_tool("emit_reform_assessment", "bad-2", _assessment(label="Made up label"))],
    )

    with pytest.raises(ReformAssessmentError, match="label"):
        assess_reform_with_catalogue(
            "Increase the personal allowance by £500",
            _intent(),
            client=client,
            search=_search,
            validate=_valid,
            catalogue_version="test-version",
        )


def test_searches_are_bounded_to_four_distinct_queries():
    calls = []

    def search(query, limit):
        calls.append((query, limit))
        return _search(query, limit)

    client = _client(
        *[
            [_tool("search_reform_targets", f"search-{index}", {"query": f"query {index}"})]
            for index in range(5)
        ],
        [_tool("emit_reform_assessment", "assessment", _assessment())],
    )

    result = assess_reform_with_catalogue(
        "Increase the personal allowance by £500",
        _intent(),
        client=client,
        search=search,
        validate=_valid,
        catalogue_version="test-version",
    )

    assert len(calls) == 4
    assert result.search_queries == tuple(f"query {index}" for index in range(4))


def test_catalogue_failure_is_not_converted_to_a_ready_assessment():
    client = _client(
        [_tool("search_reform_targets", "search-1", {"query": "personal allowance"})]
    )

    def unavailable(_query, _limit):
        raise RuntimeError("catalogue offline")

    with pytest.raises(GatewayCatalogueUnavailable):
        assess_reform_with_catalogue(
            "Increase the personal allowance by £500",
            _intent(),
            client=client,
            search=unavailable,
            validate=_valid,
            catalogue_version="test-version",
        )


def test_empty_construction_becomes_a_no_match_assessment():
    client = _client(
        [_tool("search_reform_targets", "search-1", {"query": "unknown policy"})],
        [
            _tool(
                "emit_reform_assessment",
                "assessment-1",
                {
                    "summary": "No supported parameter found",
                    "confidence": 0,
                    "reform": {},
                    "bindings": [],
                    "alternatives": [],
                },
            )
        ],
    )

    result = assess_reform_with_catalogue(
        "Increase the personal allowance by £500",
        _intent(),
        client=client,
        search=lambda _query, _limit: [],
        validate=_valid,
        catalogue_version="test-version",
    )

    assert result.reform is None
    assert result.confidence == 0


def test_low_confidence_assessment_routes_to_confirmation():
    from gateway import runtime

    assessment = SimpleNamespace(
        reform={PATH: 13_070},
        summary="Increase Personal allowance by £500",
        confidence=79,
        parameter_bindings=(),
        alternatives=(),
        search_queries=("personal allowance",),
        catalogue_version="test-version",
    )
    plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {"status": "supported"},
        "tool": "run_society_simulation",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [],
    }
    emit = SimpleNamespace(type="tool_use", name="emit_plan", input=plan)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(content=[emit]))
    )

    with (
        patch.object(runtime, "get_sync_client", return_value=client),
        patch.object(runtime, "assess_reform_with_catalogue", return_value=assessment),
    ):
        verdict = runtime.run_gateway(
            "What is the annual cost of increasing the personal allowance by £500?"
        )

    assert verdict.outcome == "needs_plan"
    assert verdict.route == "lightweight"
    assert verdict.gating_reasons[0].code == "confirm_reform"
    assert verdict.reform_assessment is assessment


def test_high_confidence_assessment_routes_to_compute():
    from gateway import runtime

    assessment = SimpleNamespace(
        reform={PATH: 13_070},
        summary="Increase Personal allowance by £500",
        confidence=80,
        parameter_bindings=(),
        alternatives=(),
        search_queries=("personal allowance",),
        catalogue_version="test-version",
    )
    plan = {
        "domain": {"status": "uk_or_unspecified"},
        "capability": {"status": "supported"},
        "tool": "run_society_simulation",
        "slots": [],
        "unmodellable_outputs": [],
        "catalogue_queries": [],
    }
    emit = SimpleNamespace(type="tool_use", name="emit_plan", input=plan)
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_kwargs: SimpleNamespace(content=[emit]))
    )

    with (
        patch.object(runtime, "get_sync_client", return_value=client),
        patch.object(runtime, "assess_reform_with_catalogue", return_value=assessment),
    ):
        verdict = runtime.run_gateway(
            "What is the annual cost of increasing the personal allowance by £500?"
        )

    assert verdict.outcome == "ready"
    assert verdict.route == "compute"
    assert verdict.reform_assessment is assessment
