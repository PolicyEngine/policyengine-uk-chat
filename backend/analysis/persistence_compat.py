"""Private readers for persisted analysis documents from earlier schemas."""

from __future__ import annotations

import json
from typing import Any

from analysis.common import (
    AnalysisError,
    AnalysisErrorCode,
    PLAN_SCHEMA_VERSION,
    WORKFLOW_SCHEMA_VERSION,
)
from analysis.models import (
    AnalysisSessionState,
    ExecutionPlan,
    PendingClarification,
    SemanticRequestRevision,
)


_LEGACY_RESULT_TYPES = {
    "get_parameter": "parameter",
    "validate_reform": "reform_validation",
    "validate_household": "household_validation",
    "run_household_simulation": "household_simulation",
    "run_society_simulation": "society_simulation",
    "compute_budgetary_impact": "budgetary_impact",
    "compute_program_breakdown": "program_breakdown",
    "compute_decile_impacts": "decile_impacts",
    "compute_winners_losers": "winners_losers",
    "compute_poverty_metrics": "poverty_metrics",
    "compute_inequality_metrics": "inequality_metrics",
    "aggregate_result": "aggregate_result",
    "generate_chart": "chart",
}


def parse_persisted(model_type, payload_json: str):
    """Validate a persisted document, upgrading version-one records on read."""
    expected_version = (
        PLAN_SCHEMA_VERSION if model_type is ExecutionPlan else WORKFLOW_SCHEMA_VERSION
    )
    try:
        payload = json.loads(payload_json)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "persisted analysis state is invalid",
        ) from exc
    actual_version = payload.get("schema_version") if isinstance(payload, dict) else None
    if actual_version == 1 and expected_version == 2:
        payload = _upgrade_version_one(model_type, payload)
    elif actual_version != expected_version:
        raise AnalysisError(
            AnalysisErrorCode.UNSUPPORTED_SCHEMA,
            (
                f"persisted {model_type.__name__} schema version "
                f"{actual_version!r} is unsupported; expected {expected_version}"
            ),
        )
    try:
        return model_type.model_validate(payload)
    except ValueError as exc:
        raise AnalysisError(
            AnalysisErrorCode.STATE_UNAVAILABLE,
            "persisted analysis state is invalid",
        ) from exc


def _upgrade_version_one(model_type, payload: dict[str, Any]) -> dict[str, Any]:
    """Adapt version-one documents without creating historical result data."""
    upgraded = dict(payload)
    upgraded["schema_version"] = WORKFLOW_SCHEMA_VERSION
    if model_type is SemanticRequestRevision:
        upgraded.pop("readiness", None)
    elif model_type is AnalysisSessionState:
        upgraded.setdefault("active_bound_request_id", None)
        upgraded.setdefault("active_execution_id", None)
        upgraded.setdefault("pending_plan_id", None)
    elif model_type is PendingClarification:
        upgraded.setdefault("target_contract", "legacy")
        upgraded.setdefault(
            "choice_mode",
            "advisory" if upgraded.get("permitted_choices") else "open",
        )
    elif model_type is ExecutionPlan:
        upgraded["schema_version"] = PLAN_SCHEMA_VERSION
        upgraded.setdefault(
            "bound_request_id",
            f"bound_legacy_{upgraded.get('request_revision_id', 'unknown')}",
        )
        upgraded.setdefault("capability_version", "1")
        upgraded["steps"] = [
            {
                **raw_step,
                "result_type": raw_step.get(
                    "result_type",
                    _LEGACY_RESULT_TYPES.get(
                        raw_step.get("operation"),
                        raw_step.get("result_binding", "unknown"),
                    ),
                ),
            }
            for raw_step in upgraded.get("steps", ())
        ]
        upgraded["operation_constraints"] = [
            {
                **raw_constraint,
                "permitted_dependency_types": raw_constraint.get(
                    "permitted_dependency_types", ()
                ),
            }
            for raw_constraint in upgraded.get("operation_constraints", ())
        ]
    return upgraded
