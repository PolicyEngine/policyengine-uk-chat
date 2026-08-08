"""Stateless signed proposal markers carried in raw assistant chat history."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from dataclasses import asdict, is_dataclass
from typing import Any

PROPOSAL_MARKER_VERSION = 1
DEFAULT_PROPOSAL_TTL_SECONDS = 24 * 60 * 60
_MARKER_RE = re.compile(r"<!--(pe-proposal:v1:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+)-->")


class ProposalSigningError(ValueError):
    """A proposal marker was absent, invalid, stale, or unverifiable."""


class ProposalExpiredError(ProposalSigningError):
    def __init__(self, envelope: dict[str, Any]):
        self.envelope = envelope
        super().__init__("proposal has expired")


def _key(value: str | None) -> bytes:
    resolved = value or os.environ.get("GATEWAY_PROPOSAL_SIGNING_KEY", "")
    encoded = resolved.encode("utf-8")
    if len(encoded) < 32:
        raise ProposalSigningError(
            "GATEWAY_PROPOSAL_SIGNING_KEY must contain at least 32 bytes"
        )
    return encoded


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except Exception as exc:
        raise ProposalSigningError("proposal payload is not valid base64url") from exc


def _canonical(value: Any) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def append_proposal_marker(
    content: str,
    proposal: dict[str, Any],
    *,
    session_id: str,
    source_prompt: str,
    signing_key: str | None = None,
    now: int | None = None,
    ttl_seconds: int = DEFAULT_PROPOSAL_TTL_SECONDS,
) -> str:
    """Append a signed, non-rendered continuation payload to clarification text."""

    issued_at = int(time.time()) if now is None else int(now)
    envelope = {
        "version": PROPOSAL_MARKER_VERSION,
        "session_id": session_id,
        "issued_at": issued_at,
        "expires_at": issued_at + ttl_seconds,
        "source_prompt_sha256": hashlib.sha256(
            source_prompt.encode("utf-8")
        ).hexdigest(),
        "proposal": proposal,
    }
    payload = _b64encode(_canonical(envelope))
    signed = f"v1.{payload}".encode("ascii")
    signature = _b64encode(hmac.new(_key(signing_key), signed, hashlib.sha256).digest())
    return content + f"\n\n<!--pe-proposal:v1:{payload}:{signature}-->"


def extract_proposal_marker(content: str) -> str:
    matches = _MARKER_RE.findall(content or "")
    if not matches:
        raise ProposalSigningError("no signed proposal marker found")
    return matches[-1]


def decode_proposal_marker(
    marker: str,
    *,
    session_id: str,
    signing_key: str | None = None,
    now: int | None = None,
) -> dict[str, Any]:
    """Verify and decode one marker without trusting client-provided history."""

    parts = marker.split(":")
    if len(parts) != 4 or parts[:2] != ["pe-proposal", "v1"]:
        raise ProposalSigningError("unsupported proposal marker format")
    payload, supplied_signature = parts[2:]
    signed = f"v1.{payload}".encode("ascii")
    expected = _b64encode(hmac.new(_key(signing_key), signed, hashlib.sha256).digest())
    if not hmac.compare_digest(supplied_signature, expected):
        raise ProposalSigningError("proposal signature is invalid")
    try:
        envelope = json.loads(_b64decode(payload))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ProposalSigningError("proposal payload is not valid JSON") from exc
    if not isinstance(envelope, dict) or envelope.get("version") != PROPOSAL_MARKER_VERSION:
        raise ProposalSigningError("unsupported proposal payload version")
    if envelope.get("session_id") != session_id:
        raise ProposalSigningError("proposal belongs to another session")
    current_time = int(time.time()) if now is None else int(now)
    expires_at = envelope.get("expires_at")
    if not isinstance(expires_at, int) or current_time > expires_at:
        raise ProposalExpiredError(envelope)
    if not isinstance(envelope.get("proposal"), dict):
        raise ProposalSigningError("proposal content is invalid")
    return envelope


def strip_proposal_markers(content: str) -> str:
    return _MARKER_RE.sub("", content or "").rstrip()


def _plain(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _plain(item) for key, item in asdict(value).items()}
    if isinstance(value, dict):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


def proposal_payload_from_verdict(verdict: Any) -> dict[str, Any]:
    """Serialize the exact low-confidence construction needed for resumption."""

    assessment = getattr(verdict, "reform_assessment", None)
    intent = getattr(verdict, "reform_intent", None)
    if assessment is None or assessment.reform is None or intent is None:
        raise ProposalSigningError("verdict has no resumable reform proposal")
    return {
        "tool": verdict.tool,
        "slots": [_plain(slot) for slot in verdict.slots],
        "reform_intent": _plain(intent),
        "assessment": _plain(assessment),
    }


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(item.get("text", ""))
            for item in content
            if isinstance(item, dict) and item.get("type") == "text"
        )
    return ""


def _active_proposal_message(
    conversation: list[dict[str, Any]],
) -> tuple[int, str] | None:
    """Return the proposal on the latest assistant turn, if there is one.

    Older markers remain in stateless chat history after they are consumed.
    Looking only at the latest assistant message prevents a later ordinary
    follow-up from reopening an already accepted proposal.
    """

    for index in range(len(conversation) - 1, -1, -1):
        message = conversation[index]
        if message.get("role") != "assistant":
            continue
        text = _message_text(message)
        return (index, text) if _MARKER_RE.search(text) else None
    return None


def _source_prompt(conversation: list[dict[str, Any]], before: int) -> str | None:
    for index in range(before - 1, -1, -1):
        if conversation[index].get("role") == "user":
            prompt = _message_text(conversation[index]).strip()
            if prompt:
                return prompt
    return None


def _latest_user(conversation: list[dict[str, Any]], after: int) -> str | None:
    for message in reversed(conversation[after + 1 :]):
        if message.get("role") == "user":
            value = _message_text(message).strip()
            if value:
                return value
    return None


_AFFIRMATIONS = {
    "yes",
    "yes please",
    "correct",
    "that's right",
    "that is right",
    "go ahead",
    "run it",
    "do it",
    "proceed",
}
_ORDINALS = {"first": 0, "second": 1, "third": 2}


def _normalise_reply(value: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9' ]+", " ", value.casefold()).split())


def _binding(value: dict[str, Any]):
    from gateway.assessment import ValidatedParameterBinding

    return ValidatedParameterBinding(
        parameter_path=value["parameter_path"],
        label=value["label"],
        catalogue_evidence=value.get("catalogue_evidence", ""),
    )


def _assessment(value: dict[str, Any], alternative_index: int | None = None):
    from gateway.assessment import ReformAlternative, ReformAssessment

    alternatives = tuple(
        ReformAlternative(
            summary=item["summary"],
            parameter_bindings=tuple(
                _binding(binding) for binding in item["parameter_bindings"]
            ),
            reform=dict(item["reform"]),
        )
        for item in value.get("alternatives", [])
    )
    if alternative_index is not None:
        if alternative_index >= len(alternatives):
            raise ProposalSigningError("selected proposal alternative does not exist")
        selected = alternatives[alternative_index]
        return ReformAssessment(
            reform=dict(selected.reform),
            summary=selected.summary,
            confidence=value["confidence"],
            parameter_bindings=selected.parameter_bindings,
            alternatives=alternatives,
            search_queries=tuple(value.get("search_queries", [])),
            catalogue_version=value["catalogue_version"],
        )
    return ReformAssessment(
        reform=dict(value["reform"]),
        summary=value.get("summary"),
        confidence=value["confidence"],
        parameter_bindings=tuple(
            _binding(binding) for binding in value["parameter_bindings"]
        ),
        alternatives=alternatives,
        search_queries=tuple(value.get("search_queries", [])),
        catalogue_version=value["catalogue_version"],
    )


def _alternative_from_reply(reply: str, assessment: dict[str, Any]) -> int | None:
    normalized = _normalise_reply(reply)
    for ordinal, index in _ORDINALS.items():
        if re.search(rf"\b{ordinal}(?: one| option)?\b", normalized):
            return index
    for index, alternative in enumerate(assessment.get("alternatives", [])):
        labels = [
            binding.get("label", "")
            for binding in alternative.get("parameter_bindings", [])
        ]
        if labels and all(label.casefold() in reply.casefold() for label in labels):
            return index
    return None


def _rebuild_ready_verdict(
    proposal: dict[str, Any],
    *,
    source_prompt: str,
    alternative_index: int | None,
):
    from gateway.execution import build_execution_plan
    from gateway.intent import ReformIntent
    from gateway.policy import SlotFact
    from gateway.runtime import GatewayVerdict

    intent = ReformIntent(**proposal["reform_intent"])
    slots = [SlotFact(**item) for item in proposal.get("slots", [])]
    assessment = _assessment(proposal["assessment"], alternative_index)
    execution = build_execution_plan(
        proposal.get("tool"),
        slots,
        intent,
        source_prompt,
        assessment,
    )
    return GatewayVerdict(
        outcome="ready",
        route="compute",
        tool=proposal.get("tool"),
        slots=slots,
        reform_intent=intent,
        reform_assessment=assessment,
        execution_plan=execution,
        proposal_resumed=True,
    )


def resume_gateway_proposal(
    conversation: list[dict[str, Any]],
    *,
    session_id: str,
    signing_key: str | None = None,
):
    """Resume an exact proposal, reassess a correction, or return ``None``."""

    found = _active_proposal_message(conversation)
    if found is None:
        return None
    assistant_index, assistant_text = found
    reply = _latest_user(conversation, assistant_index)
    source_prompt = _source_prompt(conversation, assistant_index)
    if reply is None or source_prompt is None:
        raise ProposalSigningError("proposal history is incomplete")
    marker = extract_proposal_marker(assistant_text)
    try:
        envelope = decode_proposal_marker(
            marker,
            session_id=session_id,
            signing_key=signing_key,
        )
    except ProposalExpiredError as exc:
        envelope = exc.envelope
        from gateway.runtime import run_gateway

        return run_gateway(source_prompt)
    expected_hash = hashlib.sha256(source_prompt.encode("utf-8")).hexdigest()
    if envelope.get("source_prompt_sha256") != expected_hash:
        raise ProposalSigningError("proposal source prompt does not match history")
    proposal = envelope["proposal"]

    from gateway.assessment import current_catalogue_version

    if proposal["assessment"].get("catalogue_version") != current_catalogue_version():
        from gateway.runtime import run_gateway

        return run_gateway(source_prompt)

    normalized = _normalise_reply(reply)
    if normalized in _AFFIRMATIONS:
        return _rebuild_ready_verdict(
            proposal,
            source_prompt=source_prompt,
            alternative_index=None,
        )
    alternative_index = _alternative_from_reply(reply, proposal["assessment"])
    if alternative_index is not None:
        return _rebuild_ready_verdict(
            proposal,
            source_prompt=source_prompt,
            alternative_index=alternative_index,
        )

    from gateway.intent import ReformIntent, reform_intent_from_prompt
    from gateway.policy import GatingReason, SlotFact
    from gateway.runtime import GatewayVerdict, run_gateway

    if reform_intent_from_prompt(reply) is not None:
        output = next(
            (
                slot.get("value")
                for slot in proposal.get("slots", [])
                if slot.get("kind") == "output"
            ),
            None,
        )
        suffix = f" Requested output: {output}." if output else ""
        return run_gateway(reply + suffix)
    if normalized in {"no", "no thanks", "incorrect", "that's wrong", "that is wrong"}:
        return GatewayVerdict(
            outcome="needs_plan",
            route="lightweight",
            gating_reasons=[GatingReason("missing_reform", "reform")],
        )
    return GatewayVerdict(
        outcome="needs_plan",
        route="lightweight",
        tool=proposal.get("tool"),
        slots=[SlotFact(**item) for item in proposal.get("slots", [])],
        gating_reasons=[GatingReason("confirm_reform", "reform")],
        reform_intent=ReformIntent(**proposal["reform_intent"]),
        reform_assessment=_assessment(proposal["assessment"]),
    )


def strip_proposal_markers_from_conversation(
    conversation: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return a copy with hidden metadata removed before any model call."""

    cleaned: list[dict[str, Any]] = []
    for message in conversation:
        copy = dict(message)
        content = copy.get("content")
        if isinstance(content, str):
            copy["content"] = strip_proposal_markers(content)
        elif isinstance(content, list):
            blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text":
                    block = dict(block)
                    block["text"] = strip_proposal_markers(str(block.get("text", "")))
                blocks.append(block)
            copy["content"] = blocks
        cleaned.append(copy)
    return cleaned
