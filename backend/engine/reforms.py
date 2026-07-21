"""Reform validation for the policyengine.py UK runtime."""

from __future__ import annotations

from collections.abc import Mapping
from difflib import get_close_matches
from typing import Any, Dict, List, Optional

from engine.py_runtime import uk_model_version


COMMON_REFORM_TARGETS = {
    "gov.hmrc.income_tax.allowances.personal_allowance.amount": (
        "personal allowance",
        "tax free allowance",
        "income tax allowance",
    ),
    "gov.hmrc.income_tax.rates.uk[0].rate": (
        "basic rate",
        "basic income tax rate",
    ),
    "gov.hmrc.income_tax.rates.uk[1].threshold": (
        "basic rate threshold",
        "higher rate threshold",
        "higher rate starts",
    ),
    "gov.hmrc.income_tax.rates.uk[1].rate": (
        "higher rate",
        "higher income tax rate",
    ),
    "gov.hmrc.income_tax.rates.uk[2].threshold": (
        "additional rate threshold",
        "additional rate starts",
    ),
    "gov.hmrc.income_tax.rates.uk[2].rate": (
        "additional rate",
        "additional income tax rate",
    ),
    "gov.hmrc.vat.standard_rate": (
        "standard rate of VAT",
        "VAT standard rate",
        "standard VAT rate",
    ),
    "gov.dwp.universal_credit.standard_allowance.amount.SINGLE_OLD": (
        "universal credit standard allowance",
        "UC standard allowance single over 25",
    ),
}


class ReformValidationError(ValueError):
    """Validation error carrying JSON-friendly reform errors."""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = errors
        super().__init__(errors[0]["message"] if errors else "Invalid reform")


def normalize_reform_dict(reform: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """Validate the outer reform shape and remove unset parameter values."""

    if reform is None:
        return {}
    if not isinstance(reform, Mapping):
        raise ReformValidationError(
            [
                {
                    "path": "reform",
                    "message": (
                        f"Reform must be an object mapping parameter paths to values, "
                        f"got {type(reform).__name__}."
                    ),
                }
            ]
        )

    cleaned: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    for path, value in reform.items():
        if not isinstance(path, str) or not path:
            errors.append(
                {"path": str(path), "message": "Reform parameter paths must be non-empty strings."}
            )
            continue
        if value is None:
            continue
        cleaned[path] = value
    if errors:
        raise ReformValidationError(errors)
    return cleaned


def compile_reform_dict(
    reform: Optional[Mapping[str, Any]],
    *,
    year: int,
) -> Optional[dict[str, dict[str, Any]]]:
    """Convert flat reform JSON to the policyengine.py reform structure."""

    cleaned = normalize_reform_dict(reform)
    if not cleaned:
        return None

    from policyengine.tax_benefit_models.common import compile_reform

    try:
        return compile_reform(cleaned, year=year, model_version=uk_model_version())
    except ValueError as exc:
        raise ReformValidationError(
            [{"path": "reform", "message": str(exc)}]
        ) from exc


def validate_reform_dict(
    reform: Optional[Mapping[str, Any]],
    *,
    year: int,
) -> Dict[str, Any]:
    try:
        cleaned = normalize_reform_dict(reform)
        reform_object = compile_reform_dict(cleaned, year=year) if cleaned else None
    except ReformValidationError as exc:
        return {
            "valid": False,
            "errors": exc.errors,
            "valid_targets_sample": list(COMMON_REFORM_TARGETS),
        }
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "reform", "message": f"{type(exc).__name__}: {exc}"}],
            "valid_targets_sample": list(COMMON_REFORM_TARGETS),
        }

    return {
        "valid": True,
        "normalized_reform": cleaned,
        "reform_object": reform_object,
        "parameter_paths": list(cleaned),
        "warnings": [],
    }


def search_reform_targets(query: str = "", limit: int = 25) -> list[dict[str, Any]]:
    """Search reformable parameter paths by path, label, description, or alias."""

    query_norm = (query or "").strip().lower()
    rows: list[dict[str, Any]] = []
    try:
        parameters = uk_model_version().parameters_by_name.items()
    except Exception:
        parameters = ((path, None) for path in COMMON_REFORM_TARGETS)
    for path, parameter in parameters:
        label = getattr(parameter, "label", None) if parameter is not None else None
        label = label or path
        description = getattr(parameter, "description", None) if parameter is not None else None
        aliases = COMMON_REFORM_TARGETS.get(path, ())
        haystack = " ".join([path, label or "", description or "", *aliases]).lower()
        if query_norm and query_norm not in haystack:
            close = get_close_matches(query_norm, [path.lower(), label.lower()], n=1, cutoff=0.65)
            if not close:
                continue
        rows.append(
            {
                "path": path,
                "label": label,
                "description": description,
                "aliases": list(aliases),
            }
        )
        if len(rows) >= limit:
            break
    return rows
