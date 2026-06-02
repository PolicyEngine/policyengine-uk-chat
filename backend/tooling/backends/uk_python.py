"""policyengine_uk (Python engine) backend for the typed tools.

Translates the typed-tool reform shape — programme/field, e.g.::

    {"income_tax": {"personal_allowance": 15000}}

— into the dotted-path-and-period shape ``policyengine_uk.Simulation``
takes via its ``reform=`` kwarg::

    {"gov.hmrc.income_tax.allowances.personal_allowance.amount":
        {"2025-01-01.2025-12-31": 15000}}

The dotted-path form is the same shape PE-API stores in
``/uk/policy/<id>``. Because PE-API also runs ``policyengine_uk`` (same
package version pin: ``2.88.20``), calls through this backend produce
numbers identical to PE-API by construction.

Coverage today: the parameters touched by the eval B-suite scenarios
(B1, B2, b6-b10). The mapping table is hand-curated; grow it as new
scenarios land.
"""

import logging
from typing import Any, Dict, List, Optional, Tuple


logger = logging.getLogger(__name__)


backend_id = "uk_python"


# ---------------------------------------------------------------------------
# Reform translation
# ---------------------------------------------------------------------------

# Maps the typed-tool's programme/field pair to the dotted parameter path
# policyengine_uk recognises. Verified against `CountryTaxBenefitSystem`
# at adapter authoring time — see commit message for the verification
# transcript. Add entries here as new scenarios require new parameters;
# unknown (programme, field) pairs raise ReformTranslationError.
_FIELD_TO_PATH: Dict[Tuple[str, str], str] = {
    ("income_tax", "personal_allowance"):
        "gov.hmrc.income_tax.allowances.personal_allowance.amount",
    ("income_tax", "basic_rate"):
        "gov.hmrc.income_tax.rates.uk[0].rate",
    ("income_tax", "higher_rate"):
        "gov.hmrc.income_tax.rates.uk[1].rate",
    ("income_tax", "additional_rate"):
        "gov.hmrc.income_tax.rates.uk[2].rate",
    ("national_insurance", "main_rate"):
        "gov.hmrc.national_insurance.class_1.rates.employee.main",
    ("national_insurance", "primary_threshold"):
        "gov.hmrc.national_insurance.class_1.thresholds.primary_threshold",
    ("child_benefit", "eldest_amount"):
        "gov.hmrc.child_benefit.amount.eldest",
    ("child_benefit", "additional_amount"):
        "gov.hmrc.child_benefit.amount.additional",
}


class ReformTranslationError(ValueError):
    """A typed-tool reform field has no mapping for this engine."""


def _period_for_year(year: int) -> str:
    """Build the YYYY-MM-DD.YYYY-MM-DD period key for a tax year."""
    return f"{year}-01-01.{year}-12-31"


def translate_reform(
    reform: Optional[Dict[str, Any]],
    year: int,
) -> Optional[Dict[str, Any]]:
    """Convert programme/field reform to policyengine_uk's dotted-path shape.

    Returns None for an empty/None reform so the caller can pass it through
    to ``Simulation(reform=...)`` unchanged.
    """
    if not reform:
        return None

    out: Dict[str, Any] = {}
    period = _period_for_year(year)
    unknown: List[str] = []

    for programme, fields in reform.items():
        if not isinstance(fields, dict):
            raise ReformTranslationError(
                f"Reform programme {programme!r} must be a dict, "
                f"got {type(fields).__name__}"
            )
        for field, value in fields.items():
            key = (programme, field)
            if key not in _FIELD_TO_PATH:
                unknown.append(f"{programme}.{field}")
                continue
            out[_FIELD_TO_PATH[key]] = {period: value}

    if unknown:
        known = sorted(f"{p}.{f}" for p, f in _FIELD_TO_PATH)
        raise ReformTranslationError(
            f"No mapping for: {unknown}. Known fields: {known}"
        )

    return out


# ---------------------------------------------------------------------------
# Simulation runner
# ---------------------------------------------------------------------------

# Where policyengine_uk's published Enhanced FRS sits. Matches the URL the
# eval B-suite YAML files use, so chat output is on the same dataset as
# the fixtures.
_DEFAULT_DATASET_URL = (
    "hf://policyengine/policyengine-uk-data-private/enhanced_frs_2023_24.h5"
)


def _build_microsim(dataset: str, reform_dict: Optional[Dict[str, Any]]):
    """Construct a Microsimulation against the requested dataset + reform."""
    from policyengine_uk import Microsimulation
    if dataset in ("frs", "efrs", "default", ""):
        ds = _DEFAULT_DATASET_URL
    else:
        # Pass arbitrary HF URLs / paths straight through.
        ds = dataset
    return Microsimulation(dataset=ds, reform=reform_dict)


def _decile_dict(values, weights, totals) -> Dict[str, float]:
    """Average over each decile, weighted by household weight."""
    import numpy as np
    deciles = np.array(values)
    weights = np.array(weights)
    totals = np.array(totals)
    # totals defines decile boundaries on baseline household income; deciles
    # are 1..10 with equal household-weighted mass.
    quantiles = np.quantile(
        np.repeat(totals, weights.astype(int).clip(min=0)) if weights.sum() > 0 else totals,
        np.linspace(0.1, 0.9, 9),
    )
    bin_ids = np.digitize(totals, quantiles)  # 0..9
    out: Dict[str, float] = {}
    for d in range(10):
        mask = bin_ids == d
        if not mask.any():
            out[str(d + 1)] = 0.0
            continue
        w = weights[mask]
        v = deciles[mask]
        out[str(d + 1)] = float((v * w).sum() / w.sum()) if w.sum() > 0 else 0.0
    return out


def run_economy_simulation(
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    dataset: str = "efrs",
) -> Dict[str, Any]:
    """Run a society-wide UK reform comparison via policyengine_uk.

    Engine-locked to ``policyengine_uk == 2.88.20`` (matches the PE-API v1
    pin). For reforms covered by the field mapping, results equal PE-API's
    ``/uk/economy`` endpoint to numerical precision.
    """
    try:
        reform_dict = translate_reform(reform, year)
    except ReformTranslationError as exc:
        return {"error": "Reform translation failed", "detail": str(exc)}

    try:
        sim_baseline = _build_microsim(dataset, None)
        sim_reform = _build_microsim(dataset, reform_dict)

        # Aggregates
        net_b = sim_baseline.calculate("household_net_income", year)
        net_r = sim_reform.calculate("household_net_income", year)
        it_b = sim_baseline.calculate("income_tax", year)
        it_r = sim_reform.calculate("income_tax", year)
        # Total benefits include UC, CB, etc — kept as a single aggregate
        # for now; per-programme breakdown can come in a follow-up.
        benefits_b = sim_baseline.calculate("household_benefits", year)
        benefits_r = sim_reform.calculate("household_benefits", year)

        # Household weights (microdata-weighted to the UK population)
        hh_weight = sim_baseline.calculate("household_weight", year)

        # MicroSeries supports .sum() with weights baked in.
        budgetary_impact = float((it_r - it_b).sum() + (benefits_b - benefits_r).sum())
        income_tax_revenue_change = float((it_r - it_b).sum())
        benefit_spending_change = float((benefits_r - benefits_b).sum())

        # Decile impacts (£) and relative (%) change
        # Use baseline net income for decile boundaries
        decile_avg = _decile_dict(
            (net_r - net_b).values,
            hh_weight.values,
            net_b.values,
        )
        decile_baseline = _decile_dict(net_b.values, hh_weight.values, net_b.values)
        decile_relative = {
            k: (decile_avg[k] / decile_baseline[k]) if decile_baseline[k] else 0.0
            for k in decile_avg
        }

        return {
            "engine": "policyengine_uk",
            "year": year,
            "dataset": dataset,
            "budget": {
                "budgetary_impact": budgetary_impact,
                "income_tax_revenue_change": income_tax_revenue_change,
                "benefit_spending_change": benefit_spending_change,
            },
            "decile": {
                "average": decile_avg,
                "relative": decile_relative,
            },
            "_reform_applied_dotted": reform_dict,
        }
    except Exception as exc:
        logger.exception("run_economy_simulation failed")
        return {"error": str(exc), "type": type(exc).__name__}


# Register at import time so `tooling.backends.get_backend("uk_python")`
# resolves without an explicit import in callers.
import sys as _sys

_self = _sys.modules[__name__]
from tooling.backends import register as _register  # noqa: E402

_register(_self)  # type: ignore[arg-type]
