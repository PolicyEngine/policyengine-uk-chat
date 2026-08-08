from types import SimpleNamespace

from gateway.clarifications import render_clarification, render_gating_reason
from gateway.intent import ReformIntent
from gateway.policy import GatingReason
from gateway.runtime import GatewayVerdict


def _verdict(*reasons, assessment=None, intent=None):
    return GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        gating_reasons=list(reasons),
        reform_assessment=assessment,
        reform_intent=intent,
    )


def test_missing_reform_renders_exact_question():
    verdict = _verdict(GatingReason("missing_reform", "reform"))

    assert render_clarification(verdict) == "What policy change would you like me to model?"


def test_missing_output_renders_exact_question():
    verdict = _verdict(GatingReason("missing_output", "output"))

    assert render_clarification(verdict) == (
        "What result would you like to see—for example, budgetary impact, "
        "poverty, decile impacts, or winners and losers?"
    )


def test_missing_reform_and_output_are_numbered_in_stable_order():
    verdict = _verdict(
        GatingReason("missing_reform", "reform"),
        GatingReason("missing_output", "output"),
    )

    assert render_clarification(verdict) == (
        "1. What policy change would you like me to model?\n"
        "2. What result would you like to see—for example, budgetary impact, "
        "poverty, decile impacts, or winners and losers?"
    )


def test_household_composition_has_specific_question():
    verdict = _verdict(
        GatingReason("missing_household_composition", "people")
    )

    assert render_clarification(verdict) == "What household composition should I model?"


def test_catalogue_choices_render_only_supplied_labels():
    reason = GatingReason(
        "catalogue_choice",
        "reform",
        options=("Basic rate", "Higher rate"),
    )

    rendered = render_gating_reason(reason)

    assert rendered == "Which supported parameter did you mean: “Basic rate” or “Higher rate”?"
    assert "gov." not in rendered


def test_catalogue_no_match_does_not_invent_candidates():
    reason = GatingReason("catalogue_no_match", "reform")

    assert render_gating_reason(reason) == (
        "I couldn’t identify a supported PolicyEngine parameter for that reform. "
        "Could you name the specific tax, benefit, rate, threshold, or allowance "
        "you want to change?"
    )


def test_low_confidence_confirmation_uses_catalogue_labels_not_paths():
    path = "gov.hmrc.income_tax.allowances.personal_allowance.amount"
    binding = SimpleNamespace(parameter_path=path, label="Personal allowance")
    alternative = SimpleNamespace(
        summary="Ignore this model prose",
        parameter_bindings=(
            SimpleNamespace(parameter_path="gov.other", label="Marriage allowance"),
        ),
    )
    assessment = SimpleNamespace(
        summary=f"Change {path}",
        confidence=72,
        parameter_bindings=(binding,),
        alternatives=(alternative,),
    )
    intent = ReformIntent(
        policy_phrase="personal allowance",
        action="increase",
        amount="£500",
        scope="unspecified",
        evidence="increasing the personal allowance by £500",
    )
    verdict = _verdict(
        GatingReason("confirm_reform", "reform"),
        assessment=assessment,
        intent=intent,
    )

    rendered = render_clarification(verdict)

    assert rendered == (
        "I would model this as increasing “Personal allowance” by £500. "
        "Is that what you intended?\n\n"
        "Other plausible interpretation: increasing “Marriage allowance” by £500."
    )
    assert path not in rendered
    assert "72" not in rendered
    assert "Ignore this model prose" not in rendered


def test_duplicate_questions_are_deduplicated_and_output_is_capped_at_three():
    verdict = _verdict(
        GatingReason("missing_reform", "reform"),
        GatingReason("missing_reform", "reform"),
        GatingReason("missing_output", "output"),
        GatingReason("missing_household_composition", "people"),
        GatingReason("missing_tool", "tool"),
    )

    rendered = render_clarification(verdict)

    assert rendered.count("policy change") == 1
    assert rendered.startswith("1. ")
    assert "3. What household composition" in rendered
    assert "4." not in rendered
    assert "tax-benefit calculation" not in rendered


def test_renderer_never_invents_unauthorized_topics():
    rendered = render_clarification(
        _verdict(GatingReason("missing_output", "output"))
    )

    lowered = rendered.casefold()
    for forbidden in ("year", "geography", "behavioural", "population scope"):
        assert forbidden not in lowered


def test_internal_or_unknown_reason_is_unrenderable():
    assert render_gating_reason(GatingReason("internal_slot", "year")) is None
    assert render_clarification(
        _verdict(GatingReason("internal_slot", "year"))
    ) is None
    assert render_clarification(
        _verdict(GatingReason("unknown", "mystery"))
    ) is None
