import json

import pytest

from gateway.proposals import (
    ProposalSigningError,
    append_proposal_marker,
    decode_proposal_marker,
    extract_proposal_marker,
)


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
