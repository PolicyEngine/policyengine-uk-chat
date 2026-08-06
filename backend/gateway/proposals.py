"""Stateless signed proposal markers carried in raw assistant chat history."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from typing import Any

PROPOSAL_MARKER_VERSION = 1
DEFAULT_PROPOSAL_TTL_SECONDS = 24 * 60 * 60
_MARKER_RE = re.compile(r"<!--(pe-proposal:v1:[A-Za-z0-9_-]+:[A-Za-z0-9_-]+)-->")


class ProposalSigningError(ValueError):
    """A proposal marker was absent, invalid, stale, or unverifiable."""


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
        raise ProposalSigningError("proposal has expired")
    if not isinstance(envelope.get("proposal"), dict):
        raise ProposalSigningError("proposal content is invalid")
    return envelope


def strip_proposal_markers(content: str) -> str:
    return _MARKER_RE.sub("", content or "").rstrip()
