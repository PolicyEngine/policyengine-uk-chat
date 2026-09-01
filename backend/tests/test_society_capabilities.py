from __future__ import annotations

import asyncio

import pytest

from capabilities.artifacts import SocietyAnalysisResultRef
from capabilities.chart import SocietyChartCapability
from capabilities.composition import compose_runtime
from capabilities.contracts import Completed, Failed, NeedsInput, Unsupported
from capabilities.follow_up import AnalysisFollowUpCapability
from capabilities.policy_reform import (
    PolicyReformCapability,
    ReformMeaning,
    ReformResolutionDecision,
    ReformResolutionKind,
    ResolveReformTool,
)
from capabilities.society import (
    SOCIETY_DEFAULT_OUTPUTS,
    SOCIETY_DEFAULT_PROFILE_VERSION,
    SocietyAnalysisCapability,
)
from capabilities.society_outputs import validated_aggregate_values
from tools.analysis_support import (
    ExtractResultFindingsTool,
    SelectSupportedOutputsTool,
)
from tools.contracts import CallerType
from tools.typed_dispatch import build_dispatch_tools


class MemoryArtifacts:
    def __init__(self):
        self.artifacts = []
        self.waiting = []

    async def find_artifacts(self, *, conversation_id, artifact_model):
        return tuple(
            item
            for item in self.artifacts
            if item.provenance.conversation_id == conversation_id
            and isinstance(item, artifact_model)
        )

    async def save_artifact(self, *, conversation_id, artifact):
        assert artifact.provenance.conversation_id == conversation_id
        self.artifacts.append(artifact)
        return artifact

    async def save_waiting(self, invocation):
        self.waiting.append(invocation)
        return invocation


class FakeReformResolver:
    def __init__(self):
        self.calls = []

    async def resolve(self, **kwargs):
        self.calls.append(kwargs)
        return ReformResolutionDecision(
            outcome=ReformResolutionKind.RESOLVED,
            summary="Set Example amount to £15,000.",
            reform={"gov.example.amount": 15_000},
            meaning=ReformMeaning(
                parameter_path="gov.example.amount",
                operation="set",
                value=15_000,
                unit="currency-GBP",
                effective_date="2026-01-01",
                population="all eligible UK households",
                jurisdiction="United Kingdom",
            ),
        )

    async def correct_representation(self, **kwargs):
        raise AssertionError("No correction expected")


def _budgetary_output():
    return {
        "tax_revenue": {
            "baseline": 500_000_000,
            "reform": 620_000_000,
            "change": 120_000_000,
        },
        "benefit_spending": {
            "baseline": 250_000_000,
            "reform": 270_000_000,
            "change": 20_000_000,
        },
        "net_budgetary_impact": 100_000_000,
    }


def _decile_output(decile_concept="household_net_income"):
    return {
        "decile_concept": decile_concept,
        "basis": "income",
        "income_variable": "household_net_income",
        "decile_variable": None,
        "grouping_variable": "household_net_income",
        "entity": "household",
        "quantiles": 10,
        "measure_label": "household net income",
        "grouping_label": "Household net income decile",
        "deciles": [
            {
                "decile": decile,
                "baseline_mean": decile * 10_000,
                "reform_mean": decile * 10_000 - decile * 10,
                "absolute_change": -decile * 10,
                "relative_change": -0.1,
                "count_better_off": 0,
                "count_worse_off": decile * 100_000,
                "count_no_change": (11 - decile) * 100_000,
            }
            for decile in range(1, 11)
        ],
    }


def _winners_losers_output():
    return {
        "basis": "income",
        "grouping_label": "Income decile",
        "deciles": [
            {
                "decile": decile,
                "lose_more_than_5pct": 0.01,
                "lose_less_than_5pct": 0.59,
                "no_change": 0.4,
                "gain_less_than_5pct": 0,
                "gain_more_than_5pct": 0,
            }
            for decile in range(11)
        ],
    }


def _poverty_output():
    return {
        "rates": [
            {
                "poverty_type": poverty_type,
                "group": group,
                "baseline_rate": 0.2,
                "reform_rate": 0.19,
                "rate_change": -0.01,
                "relative_change": -0.05,
                "baseline_headcount": 1_000_000,
                "reform_headcount": 950_000,
            }
            for poverty_type in (
                "absolute_ahc",
                "absolute_bhc",
                "relative_ahc",
                "relative_bhc",
            )
            for group in ("adult", "all", "child", "senior")
        ]
    }


def _inequality_output():
    return {
        "metrics": {
            metric: {
                "baseline": 0.3,
                "reform": 0.29,
                "change": -0.01,
                "relative_change": -1 / 30,
            }
            for metric in (
                "gini",
                "top_10_share",
                "top_1_share",
                "bottom_50_share",
            )
        }
    }


def _program_output():
    return {
        "programs": [
            {
                "program": "income_tax",
                "entity": "person",
                "is_tax": True,
                "baseline_total": 500_000_000,
                "reform_total": 620_000_000,
                "change": 120_000_000,
                "baseline_count": 30_000_000,
                "reform_count": 31_000_000,
                "winners": 0,
                "losers": 20_000_000,
            }
        ],
        "net_budgetary_impact": 100_000_000,
    }


def _runtime(monkeypatch):
    from tools import typed_dispatch

    calls = []
    heavy_payloads = []

    def execute(identifier, payload, context=None):
        calls.append((identifier, payload))
        if identifier == "list_supported_outputs":
            return {
                "status": "success",
                "scope": payload.get("scope"),
                "outputs": [
                    {"scope": "derivative", "name": "budgetary_impact"},
                    {"scope": "derivative", "name": "program_statistics"},
                    {"scope": "derivative", "name": "decile_impacts"},
                    {"scope": "derivative", "name": "winners_losers"},
                    {"scope": "derivative", "name": "poverty"},
                    {"scope": "derivative", "name": "inequality"},
                ],
            }
        if identifier == "list_reform_targets":
            return {
                "status": "success",
                "targets": [
                    {
                        "path": "gov.example.amount",
                        "label": "Example amount",
                    }
                ],
            }
        if identifier == "get_parameter":
            return {
                "status": "success",
                "parameter": {
                    "path": payload["path"],
                    "label": "Example amount",
                    "unit": "currency-GBP",
                    "value": 10_000,
                },
            }
        if identifier == "validate_reform":
            return {
                "valid": True,
                "normalized_reform": payload["reform"],
            }
        if identifier == "run_society_simulation":
            heavy = object()
            heavy_payloads.append(heavy)
            result_id = context.result_store.put(
                "society_simulation",
                heavy,
                {"year": payload["year"]},
            )
            return {
                "status": "success",
                "year": payload["year"],
                "result_id": result_id,
            }
        if identifier == "compute_budgetary_impact":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_budgetary_output(),
                "result_id": "budget-request-local",
            }
        if identifier == "compute_winners_losers":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_winners_losers_output(),
                "result_id": "winners-request-local",
            }
        if identifier == "compute_decile_impacts":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_decile_output(payload["decile_concept"]),
                "result_id": "decile-request-local",
            }
        if identifier == "compute_poverty_metrics":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_poverty_output(),
                "result_id": "poverty-request-local",
            }
        if identifier == "compute_inequality_metrics":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_inequality_output(),
                "result_id": "inequality-request-local",
            }
        if identifier == "compute_program_breakdown":
            return {
                "status": "success",
                "simulation_id": payload["simulation_id"],
                **_program_output(),
                "result_id": "programme-request-local",
            }
        if identifier == "generate_chart":
            return {
                "status": "success",
                "chart_markdown": "```chart\n{}\n```",
                "spec": {
                    "type": "bar",
                    "data": payload["data"],
                    "x": payload["x_field"],
                    "y": payload["y_fields"],
                },
            }
        raise AssertionError(f"Unexpected retained tool: {identifier}")

    monkeypatch.setattr(typed_dispatch, "execute_tool", execute)
    resolver = FakeReformResolver()
    artifacts = MemoryArtifacts()
    composition = compose_runtime(
        tools=[
            *build_dispatch_tools(),
            SelectSupportedOutputsTool(),
            ExtractResultFindingsTool(),
            ResolveReformTool(resolver),
        ],
        capabilities=[
            PolicyReformCapability(),
            SocietyAnalysisCapability(),
            AnalysisFollowUpCapability(),
            SocietyChartCapability(),
        ],
    )

    async def not_cancelled():
        return False

    context = composition.executor.context(
        request_id="request-1",
        conversation_id="conversation-1",
        turn_id="turn-1",
        is_cancelled=not_cancelled,
        artifacts=artifacts,
    )
    return composition, context, artifacts, calls, heavy_payloads, resolver


def _invoke(composition, context, identifier, payload):
    return asyncio.run(
        composition.executor.invoke_capability(
            identifier,
            payload,
            caller=CallerType.MODEL,
            context=context,
        )
    )


def _simulation_calls(calls):
    return [payload for identifier, payload in calls if identifier == "run_society_simulation"]


def test_baseline_run_always_calculates_and_persists_complete_default_profile(
    monkeypatch,
):
    composition, context, artifacts, calls, heavy_payloads, _resolver = _runtime(
        monkeypatch
    )

    outcome = _invoke(
        composition,
        context,
        "society_analysis",
        {},
    )

    assert isinstance(outcome, Completed)
    result = outcome.value.result
    assert result.year == 2026
    assert result.default_profile_version == SOCIETY_DEFAULT_PROFILE_VERSION
    assert result.dataset is not None
    assert result.dataset.revision == result.dataset_version
    assert result.dataset.logical_name
    assert result.dataset.title == "Enhanced FRS 2024-25"
    assert result.dataset.data_package_name == "policyengine-uk-data"
    assert result.dataset.data_package_version
    assert result.calculated_output_ids == SOCIETY_DEFAULT_OUTPUTS
    assert {value.output_id for value in result.outputs} == set(
        SOCIETY_DEFAULT_OUTPUTS
    )
    assert result.requested_output_issues == ()
    assert outcome.value.numerical_verification == "disabled"
    units = {
        (value.output_id, value.metric_id): value.unit for value in result.outputs
    }
    assert units[("decile_impacts", "deciles.relative_change")] == "percent"
    assert units[("decile_impacts", "deciles.count_worse_off")] == "people"
    assert units[("winners_losers", "deciles.lose_less_than_5pct")] == "ratio"
    simulation_input = _simulation_calls(calls)[0]
    assert set(simulation_input) == {"year"}
    assert simulation_input["year"] == 2026
    assert heavy_payloads[0] is context.result_store.get(
        next(iter(context.result_store._items)),
        "society_simulation",
    ).payload
    persisted_json = result.model_dump_json()
    assert "request-local" not in persisted_json
    assert result in artifacts.artifacts
    derivative_calls = [
        identifier
        for identifier, _payload in calls
        if identifier.startswith("compute_")
    ]
    assert derivative_calls == [
        "compute_decile_impacts",
        "compute_winners_losers",
        "compute_budgetary_impact",
    ]


def test_requested_outputs_are_additive_deduplicated_and_issues_keep_defaults(
    monkeypatch,
):
    composition, context, _artifacts, calls, _heavy, _resolver = _runtime(monkeypatch)

    outcome = _invoke(
        composition,
        context,
        "society_analysis",
        {
            "requested_outputs": [
                "poverty rate",
                "budget cost",
                "income distribution",
                "Canadian GDP",
            ]
        },
    )

    result = outcome.value.result
    assert result.calculated_output_ids == (*SOCIETY_DEFAULT_OUTPUTS, "poverty")
    assert {
        (issue.request, issue.kind) for issue in result.requested_output_issues
    } == {
        ("income distribution", "ambiguous"),
        ("Canadian GDP", "unsupported"),
    }
    identifiers = [identifier for identifier, _payload in calls]
    assert identifiers.count("compute_budgetary_impact") == 1
    assert identifiers.count("compute_poverty_metrics") == 1
    assert identifiers.count("compute_inequality_metrics") == 0


def test_ordinary_reform_is_verified_before_population_simulation(monkeypatch):
    composition, context, artifacts, calls, _heavy, resolver = _runtime(monkeypatch)

    outcome = _invoke(
        composition,
        context,
        "society_analysis",
        {"reform_instruction": "Set the Example amount to £15,000"},
    )

    assert isinstance(outcome, Completed)
    assert len(resolver.calls) == 1
    identifiers = [identifier for identifier, _payload in calls]
    assert identifiers.index("validate_reform") < identifiers.index(
        "run_society_simulation"
    )
    simulation = _simulation_calls(calls)[0]
    assert simulation["reform"] == {"gov.example.amount": 15_000}
    assert [item.artifact_type for item in artifacts.artifacts][:2] == [
        "policy_scenario",
        "society_analysis_result",
    ]


def test_reform_clarification_is_translated_to_society_partial_input(monkeypatch):
    composition, context, artifacts, calls, _heavy, resolver = _runtime(monkeypatch)

    async def clarify(**kwargs):
        resolver.calls.append(kwargs)
        return ReformResolutionDecision(
            outcome=ReformResolutionKind.NEEDS_CLARIFICATION,
            summary="Which personal allowance should change?",
            clarification="Which personal allowance should change?",
        )

    monkeypatch.setattr(resolver, "resolve", clarify)

    outcome = _invoke(
        composition,
        context,
        "society_analysis",
        {"reform_instruction": "Increase the personal allowance"},
    )

    assert isinstance(outcome, NeedsInput)
    assert outcome.prompt == "Which personal allowance should change?"
    assert outcome.partial_input == {
        "reform_instruction": "Increase the personal allowance"
    }
    assert artifacts.waiting[-1].capability_id == "society_analysis"
    assert "run_society_simulation" not in [identifier for identifier, _ in calls]


def test_follow_up_reuses_retained_aggregates_without_rerun(monkeypatch):
    composition, context, _artifacts, calls, _heavy, _resolver = _runtime(monkeypatch)
    analysis = _invoke(composition, context, "society_analysis", {})
    result_id = analysis.value.result.artifact_id
    before = len(_simulation_calls(calls))

    follow_up = _invoke(
        composition,
        context,
        "analysis_follow_up",
        {
            "question": "What does the budget result mean?",
            "referenced_result_id": result_id,
        },
    )

    assert isinstance(follow_up, Completed)
    assert follow_up.value.reran_provider is False
    assert follow_up.value.result.artifact_id == result_id
    assert follow_up.value.narration_facts == ()
    assert follow_up.value.numerical_verification == "disabled"
    assert len(_simulation_calls(calls)) == before
    assert set(follow_up.value.result.calculated_output_ids) == set(
        SOCIETY_DEFAULT_OUTPUTS
    )


def test_missing_follow_up_metric_reruns_with_full_defaults_and_new_output(monkeypatch):
    composition, context, _artifacts, calls, _heavy, _resolver = _runtime(monkeypatch)
    analysis = _invoke(composition, context, "society_analysis", {})
    result_id = analysis.value.result.artifact_id

    follow_up = _invoke(
        composition,
        context,
        "analysis_follow_up",
        {
            "question": "What about poverty?",
            "referenced_result_id": result_id,
            "requested_outputs": ["poverty rate"],
        },
    )

    assert isinstance(follow_up, Completed)
    assert follow_up.value.reran_provider is True
    assert follow_up.value.result.calculated_output_ids == (
        *SOCIETY_DEFAULT_OUTPUTS,
        "poverty",
    )
    assert len(_simulation_calls(calls)) == 2


def test_ambiguous_prior_results_require_a_local_choice(monkeypatch):
    composition, context, artifacts, _calls, _heavy, _resolver = _runtime(monkeypatch)
    first = _invoke(composition, context, "society_analysis", {})
    second = first.value.result.model_copy(update={"artifact_id": "second-result"})
    artifacts.artifacts.append(second)

    outcome = _invoke(
        composition,
        context,
        "analysis_follow_up",
        {"question": "Explain the result"},
    )

    assert isinstance(outcome, NeedsInput)
    assert first.value.result.artifact_id in outcome.prompt
    assert "second-result" in outcome.prompt


def test_chart_reuses_retained_deciles_and_persists_safe_chart_artifact(monkeypatch):
    composition, context, artifacts, calls, _heavy, _resolver = _runtime(monkeypatch)
    analysis = _invoke(composition, context, "society_analysis", {})
    before = len(_simulation_calls(calls))

    chart = _invoke(
        composition,
        context,
        "society_chart",
        {
            "referenced_result_id": analysis.value.result.artifact_id,
            "requested_output": "deciles",
            "title": "Income-decile impacts",
        },
    )

    assert isinstance(chart, Completed)
    assert len(_simulation_calls(calls)) == before
    assert chart.value.chart.source_result_artifact_id == (
        analysis.value.result.artifact_id
    )
    assert chart.value.chart.presentation.chart_type == "generic_bar"
    assert "request-local" not in chart.value.chart.model_dump_json()
    assert artifacts.artifacts[-1] == chart.value.chart


def test_chart_reruns_when_requested_metric_was_not_retained(monkeypatch):
    composition, context, _artifacts, calls, _heavy, _resolver = _runtime(monkeypatch)
    analysis = _invoke(composition, context, "society_analysis", {})

    chart = _invoke(
        composition,
        context,
        "society_chart",
        {
            "referenced_result_id": analysis.value.result.artifact_id,
            "requested_output": "poverty",
        },
    )

    assert isinstance(chart, Completed)
    assert len(_simulation_calls(calls)) == 2
    assert "poverty" in chart.value.source_result.calculated_output_ids


def test_incompatible_dataset_result_is_not_combined(monkeypatch):
    composition, context, artifacts, _calls, _heavy, _resolver = _runtime(monkeypatch)
    analysis = _invoke(composition, context, "society_analysis", {})
    stale = analysis.value.result.model_copy(
        update={"artifact_id": "stale", "dataset_version": "old-dataset"}
    )
    artifacts.artifacts.append(stale)

    outcome = _invoke(
        composition,
        context,
        "analysis_follow_up",
        {
            "question": "Explain this",
            "referenced_result_id": "stale",
        },
    )

    assert isinstance(outcome, Unsupported)


def test_decile_projection_rejects_incomplete_or_non_finite_values():
    incomplete = _decile_output()
    incomplete["deciles"] = incomplete["deciles"][:-1]
    with pytest.raises(ValueError, match="each decile"):
        validated_aggregate_values("decile_impacts", incomplete)

    non_finite = _decile_output()
    non_finite["deciles"][0]["baseline_mean"] = float("nan")
    with pytest.raises(ValueError, match="finite number"):
        validated_aggregate_values("decile_impacts", non_finite)


def test_invalid_society_output_fails_before_artifact_persistence(monkeypatch):
    composition, context, artifacts, _calls, _heavy, _resolver = _runtime(monkeypatch)

    def reject_deciles(output_id, payload):
        if output_id == "decile_impacts":
            raise ValueError("invalid deciles")
        return validated_aggregate_values(output_id, payload)

    monkeypatch.setattr(
        "capabilities.society.validated_aggregate_values",
        reject_deciles,
    )

    outcome = _invoke(composition, context, "society_analysis", {})

    assert isinstance(outcome, Failed)
    assert outcome.error_code == "society_output_validation_failed"
    assert not any(
        isinstance(artifact, SocietyAnalysisResultRef)
        for artifact in artifacts.artifacts
    )
