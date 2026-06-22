"""Parametric reform validation and compiled-policy construction."""

from typing import Any, Dict, List, Optional, Tuple

from engine.simulations import ensure_compiled_package_importable


DEFAULT_VALID_PROGRAMS = [
    "income_tax",
    "national_insurance",
    "universal_credit",
    "child_benefit",
    "state_pension",
    "pension_credit",
    "benefit_cap",
    "housing_benefit",
    "tax_credits",
    "scottish_child_payment",
    "stamp_duty",
    "capital_gains_tax",
    "wealth_tax",
]

class ReformValidationError(ValueError):
    """Validation error carrying JSON-friendly reform errors."""

    def __init__(self, errors: List[Dict[str, str]]):
        self.errors = errors
        message = errors[0]["message"] if errors else "Invalid reform"
        super().__init__(message)


def _parameter_classes():
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import (
        BenefitCapParams,
        CapitalGainsTaxParams,
        ChildBenefitParams,
        HousingBenefitParams,
        IncomeTaxParams,
        NationalInsuranceParams,
        PensionCreditParams,
        ScottishChildPaymentParams,
        StampDutyBand,
        StampDutyParams,
        StatePensionParams,
        TaxCreditsParams,
        UniversalCreditParams,
        WealthTaxParams,
    )

    return (
        {
            "income_tax": IncomeTaxParams,
            "national_insurance": NationalInsuranceParams,
            "universal_credit": UniversalCreditParams,
            "child_benefit": ChildBenefitParams,
            "state_pension": StatePensionParams,
            "pension_credit": PensionCreditParams,
            "benefit_cap": BenefitCapParams,
            "housing_benefit": HousingBenefitParams,
            "tax_credits": TaxCreditsParams,
            "scottish_child_payment": ScottishChildPaymentParams,
            "stamp_duty": StampDutyParams,
            "capital_gains_tax": CapitalGainsTaxParams,
            "wealth_tax": WealthTaxParams,
        },
        StampDutyParams,
        StampDutyBand,
    )


def get_valid_programs() -> List[str]:
    try:
        param_cls_map, _, _ = _parameter_classes()
    except ModuleNotFoundError:
        return DEFAULT_VALID_PROGRAMS
    return list(param_cls_map)


def build_reform_schema(valid_programs: Optional[List[str]] = None) -> Dict[str, Any]:
    programs = valid_programs or get_valid_programs()
    return {
        "type": "object",
        "description": (
            "Parametric reform. Top-level keys are programmes; values are the "
            "parameter changes for that programme. Valid programmes include "
            f"{', '.join(programs[:-1])}, and {programs[-1]}. "
            "Field names within each programme match the corresponding *Params "
            "constructor. For structural reforms, use run_python instead."
        ),
        "additionalProperties": True,
    }


REFORM_SCHEMA = build_reform_schema()


def normalise_reform(
    reform: Optional[Dict[str, Any]],
) -> Tuple[Dict[str, Dict[str, Any]], Dict[str, Any]]:
    """Validate and normalize a reform dict, returning JSON and model objects."""
    if not reform:
        return {}, {}
    if not isinstance(reform, dict):
        raise ReformValidationError(
            [{"path": "reform", "message": f"Reform must be a dict, got {type(reform).__name__}"}]
        )

    param_cls_map, stamp_duty_cls, stamp_duty_band_cls = _parameter_classes()
    normalized: Dict[str, Dict[str, Any]] = {}
    model_kwargs: Dict[str, Any] = {}
    errors: List[Dict[str, str]] = []

    for program, fields in reform.items():
        if program not in param_cls_map:
            errors.append(
                {
                    "path": str(program),
                    "message": f"Unknown reform program '{program}'. Valid: {list(param_cls_map)}",
                }
            )
            continue
        if not isinstance(fields, dict):
            errors.append(
                {
                    "path": str(program),
                    "message": f"Reform program '{program}' must be a dict, got {type(fields).__name__}",
                }
            )
            continue

        cls = param_cls_map[program]
        valid_fields = set(cls.model_fields)
        unknown = sorted(k for k in fields if k not in valid_fields and fields[k] is not None)
        if unknown:
            for field in unknown:
                errors.append(
                    {
                        "path": f"{program}.{field}",
                        "message": (
                            f"Unknown field(s) for '{program}': {unknown}. "
                            f"Valid: {sorted(valid_fields)}"
                        ),
                    }
                )
            continue

        cleaned_fields = {k: v for k, v in fields.items() if v is not None}
        model_fields = dict(cleaned_fields)
        if cls is stamp_duty_cls and "bands" in model_fields:
            model_fields["bands"] = [
                stamp_duty_band_cls(**band) if isinstance(band, dict) else band
                for band in model_fields["bands"]
            ]
        try:
            model_kwargs[program] = cls(**model_fields)
        except Exception as exc:
            errors.append({"path": str(program), "message": f"{type(exc).__name__}: {exc}"})
            continue
        if cleaned_fields:
            normalized[program] = cleaned_fields

    if errors:
        raise ReformValidationError(errors)
    return normalized, model_kwargs


def build_compiled_policy(reform: Optional[Dict[str, Any]]):
    normalized, model_kwargs = normalise_reform(reform)
    if not normalized:
        return None
    ensure_compiled_package_importable()
    from policyengine_uk_compiled import Parameters

    return Parameters(**model_kwargs)


def validate_reform_dict(reform: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    try:
        normalized, _ = normalise_reform(reform)
    except ReformValidationError as exc:
        return {"valid": False, "errors": exc.errors, "valid_programs": get_valid_programs()}
    except Exception as exc:
        return {
            "valid": False,
            "errors": [{"path": "reform", "message": f"{type(exc).__name__}: {exc}"}],
            "valid_programs": get_valid_programs(),
        }

    return {
        "valid": True,
        "normalized_reform": normalized,
        "programs": list(normalized),
        "warnings": [],
    }
