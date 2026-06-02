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


def run_economy_simulation(
    year: int = 2025,
    reform: Optional[Dict[str, Any]] = None,
    dataset: str = "efrs",
) -> Dict[str, Any]:
    """Run a society-wide UK reform comparison via policyengine_uk.

    TEMP / TECH DEBT — see https://github.com/PolicyEngine/policyengine-uk-chat
        This function inlines ~50 lines of glue that ARE ALREADY IMPLEMENTED in
        ``policyengine.outputs.macro.comparison.calculate_economy_comparison``.
        The right long-term shape is::

            from policyengine.outputs.macro.comparison.calculate_economy_comparison \\
                import calculate_economy_comparison
            return calculate_economy_comparison(sim).model_dump()

        We can't do that today because ``pip install policyengine==0.13.0``
        transitively requires ``policyengine_us``, which pins
        ``policyengine_core>=3.26.0``, which conflicts with the precise
        ``policyengine_core==3.25.3`` the orchestrator at 0.13.0 was built
        against. Resolution requires either (a) PolicyEngine releasing a
        coherent triplet of orchestrator + uk + core, or (b) the orchestrator
        making country backends optional installs (``policyengine[uk]``).
        Until one of those lands, we mirror the methodology by hand so the
        chat backend's deps stay tight. Drop this block when the
        orchestrator imports cleanly.

    Mirrors the methodology in ``calculate_economy_comparison`` line-by-line
    so output values match PE-API's ``/uk/economy`` endpoint:

    - Total tax ``gov_tax``, total spending ``gov_spending`` for UK.
    - Decile groupby on ``household_income_decile``, average =
      sum(change) / count(households) per bin.
    - Poverty: person-level ``in_poverty`` with ``map_to='person'``,
      grouped by ``age < 18`` (child), ``18..64`` (adult), ``>= 65``
      (senior), weighted mean by person_weight.

    Engine-locked to ``policyengine_uk == 2.88.20``.
    """
    # TODO(tech-debt): Replace body with calculate_economy_comparison(sim).model_dump()
    # once the policyengine orchestrator dependency resolves cleanly. The output
    # shape we build below is a subset of what the orchestrator returns.
    try:
        reform_dict = translate_reform(reform, year)
    except ReformTranslationError as exc:
        return {"error": "Reform translation failed", "detail": str(exc)}

    try:
        from microdf import MicroSeries

        sim_b = _build_microsim(dataset, None)
        sim_r = _build_microsim(dataset, reform_dict)

        # --- Budget (mirrors budgetary_impact) ---
        gov_tax_b = float(sim_b.calculate("gov_tax", year).sum())
        gov_tax_r = float(sim_r.calculate("gov_tax", year).sum())
        gov_spend_b = float(sim_b.calculate("gov_spending", year).sum())
        gov_spend_r = float(sim_r.calculate("gov_spending", year).sum())

        tax_revenue_impact = gov_tax_r - gov_tax_b
        benefit_spending_impact = gov_spend_r - gov_spend_b
        budgetary_impact = tax_revenue_impact - benefit_spending_impact

        # --- Decile (mirrors decile_impact) ---
        hh_weight = sim_b.calculate("household_weight", year)
        net_b = MicroSeries(
            sim_b.calculate("household_net_income", year).values,
            weights=hh_weight.values,
        )
        net_r = MicroSeries(
            sim_r.calculate("household_net_income", year).values,
            weights=hh_weight.values,
        )
        decile = MicroSeries(sim_b.calculate("household_income_decile", year).values)

        # Filter out the -1 sentinel
        mask_valid = decile >= 0
        net_b_f = net_b[mask_valid]
        net_r_f = net_r[mask_valid]
        decile_f = decile[mask_valid]

        income_change = net_r_f - net_b_f
        rel_by_decile = (
            income_change.groupby(decile_f).sum()
            / net_b_f.groupby(decile_f).sum()
        )
        avg_by_decile = (
            income_change.groupby(decile_f).sum()
            / net_b_f.groupby(decile_f).count()
        )

        # --- Poverty (mirrors poverty_impact) ---
        person_weight = sim_b.calculate("person_weight", year)
        person_in_pov_b = sim_b.calculate("in_poverty", year, map_to="person")
        person_in_pov_r = sim_r.calculate("in_poverty", year, map_to="person")
        age = MicroSeries(sim_b.calculate("age", year).values)

        # baseline_poverty uses person_weight for both baseline and reform —
        # PE-API freezes weights on the baseline so reform comparisons are
        # apples-to-apples.
        pov_b = MicroSeries(person_in_pov_b.values, weights=person_weight.values)
        pov_r = MicroSeries(person_in_pov_r.values, weights=person_weight.values)

        def pov_group(s: MicroSeries, mask) -> float:
            return float(s[mask].mean())

        poverty = {
            "child": {
                "baseline": pov_group(pov_b, age < 18),
                "reform": pov_group(pov_r, age < 18),
            },
            "adult": {
                "baseline": pov_group(pov_b, (age >= 18) & (age < 65)),
                "reform": pov_group(pov_r, (age >= 18) & (age < 65)),
            },
            "senior": {
                "baseline": pov_group(pov_b, age >= 65),
                "reform": pov_group(pov_r, age >= 65),
            },
            "all": {
                "baseline": float(pov_b.mean()),
                "reform": float(pov_r.mean()),
            },
        }

        return {
            "engine": "policyengine_uk",
            "year": year,
            "dataset": dataset,
            "budget": {
                "budgetary_impact": budgetary_impact,
                "tax_revenue_impact": tax_revenue_impact,
                "benefit_spending_impact": benefit_spending_impact,
            },
            "decile": {
                "average": {int(k): float(v) for k, v in avg_by_decile.to_dict().items()},
                "relative": {int(k): float(v) for k, v in rel_by_decile.to_dict().items()},
            },
            "poverty": {"poverty": poverty},
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
