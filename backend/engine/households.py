"""Illustrative household validation and calculation helpers for .py."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Dict, Optional

from engine.constants import HOUSEHOLD_COUNTRY_IDS
from engine.py_runtime import calculate_household_py, uk_model_version
from engine.reforms import (
    ReformValidationError,
    compile_reform_dict,
    normalize_reform_dict,
)
from engine.serialization import json_safe


def validate_household_dict(
    *,
    people: list[dict[str, Any]],
    benunit: dict[str, Any] | None = None,
    household: dict[str, Any] | None = None,
    year: int,
    reform: Optional[Mapping[str, Any]] = None,
    extra_variables: Optional[list[str]] = None,
) -> Dict[str, Any]:
    household = household or {}
    if "country" in household and household["country"] not in HOUSEHOLD_COUNTRY_IDS:
        valid_values = ", ".join(HOUSEHOLD_COUNTRY_IDS)
        return {
            "valid": False,
            "errors": [
                {
                    "path": "household.country",
                    "message": (
                        f"country must be one of {valid_values}; "
                        f"received {household['country']!r}."
                    ),
                }
            ],
        }

    try:
        from policyengine.tax_benefit_models.common import (
            dispatch_extra_variables,
            validate_annual_household_inputs,
        )
        from policyengine.utils.household_validation import validate_household_input

        year = validate_annual_household_inputs(
            year=year,
            entities={
                "people": people,
                "benunit": [benunit or {}],
                "household": [household],
            },
        )
        validate_household_input(
            model_version=uk_model_version(),
            entities={
                "person": people,
                "benunit": [benunit or {}],
                "household": [household],
            },
        )
        extra_by_entity = dispatch_extra_variables(
            model_version=uk_model_version(),
            names=extra_variables or [],
        )
        normalized_reform = normalize_reform_dict(reform)
        reform_object = (
            compile_reform_dict(normalized_reform, year=year)
            if normalized_reform
            else None
        )
    except ReformValidationError as exc:
        return {"valid": False, "errors": exc.errors}
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "household", "message": f"{type(exc).__name__}: {exc}"}],
        }
    return {
        "valid": True,
        "year": year,
        "people_count": len(people),
        "extra_variables_by_entity": extra_by_entity,
        "normalized_reform": normalized_reform,
        "reform_object": reform_object,
        "warnings": [],
    }


def calculate_household(
    *,
    people: list[dict[str, Any]],
    benunit: dict[str, Any] | None,
    household: dict[str, Any] | None,
    year: int,
    reform: Optional[Mapping[str, Any]] = None,
    extra_variables: Optional[list[str]] = None,
) -> Dict[str, Any]:
    validation = validate_household_dict(
        people=people,
        benunit=benunit,
        household=household,
        year=year,
        reform=reform,
        extra_variables=extra_variables,
    )
    if not validation.get("valid"):
        return {"error": validation["errors"][0]["message"], "validation": validation}
    normalized_reform = validation["normalized_reform"]

    try:
        baseline = calculate_household_py(
            people=people,
            benunit=benunit,
            household=household,
            year=year,
            extra_variables=extra_variables,
        )
        if normalized_reform:
            reformed = calculate_household_py(
                people=people,
                benunit=benunit,
                household=household,
                year=year,
                reform=normalized_reform,
                extra_variables=extra_variables,
            )
        else:
            reformed = baseline
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}

    baseline_safe = json_safe(baseline)
    reform_safe = json_safe(reformed)
    response: Dict[str, Any] = {
        "status": "success",
        "year": year,
        "reform_applied": bool(normalized_reform),
    }
    if not normalized_reform:
        response.update(baseline_safe)
    else:
        response["baseline"] = baseline_safe
        response["reform"] = reform_safe
    return response
