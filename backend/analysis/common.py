"""Version contracts, canonical serialization, identifiers, and typed errors."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import NAMESPACE_URL, uuid4, uuid5

WORKFLOW_SCHEMA_VERSION = 2
PLAN_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class RuntimeVersions:
    """Deployment-defined values shared by binding, compilation, and execution."""

    catalogue_version: str
    engine_version: str
    country_package_version: str
    dataset_identifier: str
    plan_schema_version: int = PLAN_SCHEMA_VERSION


class AnalysisErrorCode(StrEnum):
    INVALID_CANDIDATE = "invalid_candidate"
    INVALID_CANDIDATE_TYPE = "invalid_candidate_type"
    INVALID_EVIDENCE = "invalid_evidence"
    CAPABILITY_INVALID = "capability_invalid"
    STATE_PRECONDITION_FAILED = "state_precondition_failed"
    LIFECYCLE_PRECONDITION_FAILED = "lifecycle_precondition_failed"
    STATE_CONFLICT = "state_conflict"
    STATE_UNAVAILABLE = "state_unavailable"
    UNSUPPORTED_SCHEMA = "unsupported_schema"
    BINDING_FAILED = "binding_failed"
    CATALOGUE_UNAVAILABLE = "catalogue_unavailable"
    CLARIFICATION_REQUIRED = "clarification_required"
    REQUEST_UNSUPPORTED = "request_unsupported"
    PLAN_INVALID = "plan_invalid"
    PLAN_STALE = "plan_stale"
    EXECUTION_FAILED = "execution_failed"
    EXECUTION_CANCELLED = "execution_cancelled"
    EXECUTION_CONFLICT = "execution_conflict"
    EXECUTION_TOKEN_INVALID = "execution_token_invalid"
    EXECUTION_EXPIRED = "execution_expired"
    OPERATION_NOT_PERMITTED = "operation_not_permitted"
    RESULT_INVALID = "result_invalid"
    RESOURCE_LIMIT = "resource_limit"
    REQUIRED_RESULTS_MISSING = "required_results_missing"
    NARRATION_INVALID = "narration_invalid"
    OUTCOME_INVALID = "outcome_invalid"
    IDEMPOTENCY_CONFLICT = "idempotency_conflict"


class AnalysisError(RuntimeError):
    """An expected analysis-runtime failure with a stable machine code."""

    def __init__(self, code: AnalysisErrorCode, message: str, *, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def canonical_json(value: Any) -> str:
    """Return deterministic JSON for hashing and persisted contracts."""

    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", exclude_none=True)
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def new_identifier(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex}"


def stable_identifier(prefix: str, *parts: object) -> str:
    """Return a reproducible identifier for idempotent state transitions."""

    payload = canonical_json([str(part) for part in parts])
    return f"{prefix}_{uuid5(NAMESPACE_URL, payload).hex}"
