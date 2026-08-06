import json

import pytest

from gateway.proposals import (
    ProposalSigningError,
    append_proposal_marker,
    decode_proposal_marker,
    extract_proposal_marker,
    proposal_payload_from_verdict,
    resume_gateway_proposal,
    strip_proposal_markers_from_conversation,
)
from gateway.assessment import (
    ReformAlternative,
    ReformAssessment,
    ValidatedParameterBinding,
)
from gateway.intent import ReformIntent
from gateway.policy import GatingReason, SlotFact
from gateway.runtime import GatewayVerdict


KEY = "test-signing-key-that-is-at-least-32-bytes-long"


def _payload():
    return {
        "confidence": 72,
        "catalogue_version": "2026.1",
        "reform": {"gov.hmrc.income_tax.rates.uk[0].rate": 0.21},
        "bindings": [
            {
                "parameter_path": "gov.hmrc.income_tax.rates.uk[0].rate",
                "label": "Basic rate",
            }
        ],
        "alternatives": [],
    }


def test_signed_proposal_round_trip_is_session_bound_and_expiring():
    content = append_proposal_marker(
        "Is that what you intended?",
        _payload(),
        session_id="session-1",
        source_prompt="Increase the basic rate by one percentage point",
        signing_key=KEY,
        now=1_000,
        ttl_seconds=60,
    )

    marker = extract_proposal_marker(content)
    decoded = decode_proposal_marker(
        marker,
        session_id="session-1",
        signing_key=KEY,
        now=1_030,
    )

    assert decoded["proposal"] == _payload()
    assert decoded["source_prompt_sha256"]
    assert decoded["issued_at"] == 1_000
    assert decoded["expires_at"] == 1_060
    assert "pe-proposal" in content


def test_payload_is_signed_but_not_encrypted():
    content = append_proposal_marker(
        "Confirm",
        _payload(),
        session_id="session-1",
        source_prompt="prompt",
        signing_key=KEY,
        now=1_000,
    )
    marker = extract_proposal_marker(content)
    encoded_payload = marker.split(":")[2]

    import base64

    raw = base64.urlsafe_b64decode(encoded_payload + "=" * (-len(encoded_payload) % 4))
    assert json.loads(raw)["proposal"]["bindings"][0]["label"] == "Basic rate"


@pytest.mark.parametrize("mutation", ["signature", "payload", "session", "expired"])
def test_modified_wrong_session_or_expired_proposals_are_rejected(mutation):
    content = append_proposal_marker(
        "Confirm",
        _payload(),
        session_id="session-1",
        source_prompt="prompt",
        signing_key=KEY,
        now=1_000,
        ttl_seconds=60,
    )
    marker = extract_proposal_marker(content)
    session_id = "session-1"
    now = 1_030
    if mutation == "signature":
        marker = marker[:-1] + ("A" if marker[-1] != "A" else "B")
    elif mutation == "payload":
        parts = marker.split(":")
        parts[2] = ("A" if parts[2][0] != "A" else "B") + parts[2][1:]
        marker = ":".join(parts)
    elif mutation == "session":
        session_id = "session-2"
    else:
        now = 1_061

    with pytest.raises(ProposalSigningError):
        decode_proposal_marker(
            marker,
            session_id=session_id,
            signing_key=KEY,
            now=now,
        )


def test_signing_key_is_required_and_long_enough():
    with pytest.raises(ProposalSigningError):
        append_proposal_marker(
            "Confirm",
            _payload(),
            session_id="session-1",
            source_prompt="prompt",
            signing_key="short",
        )


def _low_confidence_verdict():
    best = ValidatedParameterBinding("path.best", "Best label", "best")
    other = ValidatedParameterBinding("path.other", "Other label", "other")
    assessment = ReformAssessment(
        reform={"path.best": 0.21},
        summary="Best proposal",
        confidence=72,
        parameter_bindings=(best,),
        alternatives=(
            ReformAlternative("Other proposal", (other,), {"path.other": 0.22}),
        ),
        search_queries=("basic rate",),
        catalogue_version="test-version",
    )
    return GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        tool="run_society_simulation",
        slots=[
            SlotFact("output", "prompt", kind="output", value="budgetary_impact")
        ],
        gating_reasons=[GatingReason("confirm_reform", "reform")],
        reform_intent=ReformIntent(
            policy_phrase="basic rate",
            action="increase",
            amount="one percentage point",
            scope="unspecified",
            evidence="increasing the basic rate by one percentage point",
        ),
        reform_assessment=assessment,
    )


def _proposal_conversation(reply="yes"):
    prompt = "What is the cost of increasing the basic rate by one percentage point?"
    payload = proposal_payload_from_verdict(_low_confidence_verdict())
    assistant = append_proposal_marker(
        "I would model this as increasing “Best label” by one percentage point.",
        payload,
        session_id="session-1",
        source_prompt=prompt,
        signing_key=KEY,
    )
    return [
        {"role": "user", "content": prompt},
        {"role": "assistant", "content": assistant},
        {"role": "user", "content": reply},
    ]


def test_affirmative_followup_reuses_exact_proposal(monkeypatch):
    import gateway.assessment as assessment

    monkeypatch.setattr(assessment, "current_catalogue_version", lambda: "test-version")

    verdict = resume_gateway_proposal(
        _proposal_conversation(),
        session_id="session-1",
        signing_key=KEY,
    )

    assert verdict.outcome == "ready"
    assert verdict.proposal_resumed is True
    assert verdict.execution_plan.approved_reform == {"path.best": 0.21}
    assert verdict.reform_assessment.confidence == 72


def test_ordinal_followup_reuses_exact_alternative(monkeypatch):
    import gateway.assessment as assessment

    monkeypatch.setattr(assessment, "current_catalogue_version", lambda: "test-version")

    verdict = resume_gateway_proposal(
        _proposal_conversation("the first option"),
        session_id="session-1",
        signing_key=KEY,
    )

    assert verdict.execution_plan.approved_reform == {"path.other": 0.22}


def test_proposal_markers_are_removed_before_model_calls():
    conversation = _proposal_conversation("maybe")

    cleaned = strip_proposal_markers_from_conversation(conversation)

    assert "pe-proposal" in conversation[1]["content"]
    assert "pe-proposal" not in cleaned[1]["content"]
    assert "Best label" in cleaned[1]["content"]


def test_consumed_proposal_is_not_reopened_by_later_followup():
    conversation = _proposal_conversation("yes")
    conversation.extend(
        [
            {"role": "assistant", "content": "The calculated result."},
            {"role": "user", "content": "What about poverty?"},
        ]
    )

    assert (
        resume_gateway_proposal(
            conversation,
            session_id="session-1",
            signing_key=KEY,
        )
        is None
    )
