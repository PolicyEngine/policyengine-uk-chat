#!/usr/bin/env python3
"""
Build reference fixtures for Test B scenarios.

For each blog-grounded scenario:
  1. Fetch reform JSONs from PE-API's /uk/policy/<id> endpoint (this works
     even when the /economy endpoint is down — it's a DB read).
  2. Run the reform locally via `policyengine.Simulation +
     calculate_economy_comparison` against EFRS 2023-24 (downloaded from HF).
  3. Compare against the published figures in Vahid's blog post. Apply a
     drift threshold: keep fields whose locally-computed value is within
     tolerance of the published figure, drop the rest.
  4. Write a fixture JSON containing only the kept fields + a sibling
     drift_report.md listing kept/dropped/why for human review.

For scenarios with no published source (B1 PA reform, B3 household calc,
B4 MTR schedule) the local computation IS the fixture — we record what the
engine produces and use it as the reference.

Generated fixtures are committed to git so the grader doesn't refetch on
every CI run. Re-run this script when scenarios change or to bump engine
versions; expect dropped fields to change as PolicyEngine UK's current-law
baseline drifts.

Usage:
    python evals/runner/build_fixtures.py                    # all scenarios
    python evals/runner/build_fixtures.py b1 b3              # just these
    python evals/runner/build_fixtures.py --validate-only    # don't rebuild,
                                                              # check that
                                                              # scenario YAML
                                                              # paths resolve
                                                              # in existing
                                                              # fixtures
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import httpx
import yaml


# Paths
EVALS_DIR = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
FIXTURES_DIR = EVALS_DIR / "fixtures" / "pe_api"

PE_API_BASE = "https://api.policyengine.org"
HF_DATASET = "policyengine/policyengine-uk-data-private"
HF_FILE = "enhanced_frs_2023_24.h5"

# Drift threshold: when comparing our locally-computed numbers to Vahid's
# published figures, accept fields whose magnitude is within 10% of the
# published value. Larger drift = engine baseline has moved since publication,
# field is dropped from the fixture with a note.
DRIFT_TOLERANCE_PCT = 10.0


# ---------------------------------------------------------------------------
# Lazy heavy imports (so --validate-only and --help don't pay for them)
# ---------------------------------------------------------------------------

def _import_pe():
    """Import the policyengine stack. Only needed when actually building."""
    from policyengine import Simulation
    from policyengine.outputs.macro.comparison.calculate_economy_comparison import (
        calculate_economy_comparison,
    )
    from policyengine_core.tools.hugging_face import download_huggingface_dataset
    return Simulation, calculate_economy_comparison, download_huggingface_dataset


_DATASET_PATH_CACHE: str | None = None


def get_dataset_path() -> str:
    global _DATASET_PATH_CACHE
    if _DATASET_PATH_CACHE is None:
        _, _, download = _import_pe()
        _DATASET_PATH_CACHE = download(repo=HF_DATASET, repo_filename=HF_FILE)
    return _DATASET_PATH_CACHE


# ---------------------------------------------------------------------------
# PE-API helpers
# ---------------------------------------------------------------------------

def fetch_reform_json(reform_id: int) -> dict[str, Any]:
    """Pull a reform's policy_json from PE-API's policy endpoint.

    /uk/policy/<id> is a DB read — works even when /economy is broken.
    """
    r = httpx.get(f"{PE_API_BASE}/uk/policy/{reform_id}", timeout=30.0)
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"policy fetch failed for {reform_id}: {body}")
    return body["result"]["policy_json"] or {}


# ---------------------------------------------------------------------------
# Local sim runner
# ---------------------------------------------------------------------------

def run_economy(
    *,
    reform: dict[str, Any] | None,
    baseline: dict[str, Any] | None,
    time_period: int,
) -> dict[str, Any]:
    """Run reform-vs-baseline through the policyengine package and return the
    EconomyComparison output as a dict."""
    Simulation, calculate_economy_comparison, _ = _import_pe()
    sim = Simulation(
        country="uk",
        scope="macro",
        data=get_dataset_path(),
        time_period=time_period,
        region="uk",
        reform=reform,
        baseline=baseline,
    )
    return calculate_economy_comparison(sim).model_dump()


def run_household(situation: dict[str, Any], year: int) -> dict[str, float]:
    """Compute single-household figures via policyengine_uk directly."""
    from policyengine_uk import Simulation as UKSimulation
    sim = UKSimulation(situation=situation)
    return {
        "household_net_income": float(sim.calculate("household_net_income", year)[0]),
        "income_tax": float(sim.calculate("income_tax", year)[0]),
        "national_insurance": float(sim.calculate("national_insurance", year)[0]),
    }


def mtr_at(year: int, gross_income: int) -> dict[str, float]:
    """Combined IT + NI marginal tax rate at a single income point.

    Computed by finite difference: tax at (gross + £100) − tax at (gross),
    divided by 100 to get pp.
    """
    def at(income: int) -> tuple[float, float]:
        from policyengine_uk import Simulation as UKSimulation
        sit = {
            "people": {"p": {"age": 35, "employment_income": income}},
            "benunits": {"b": {"members": ["p"]}},
            "households": {"h": {"members": ["p"]}},
        }
        sim = UKSimulation(situation=sit)
        return (
            float(sim.calculate("income_tax", year)[0]),
            float(sim.calculate("national_insurance", year)[0]),
        )

    it_a, ni_a = at(gross_income)
    it_b, ni_b = at(gross_income + 100)
    return {
        "gross": gross_income,
        "it_mtr": round(it_b - it_a, 2),
        "ni_mtr": round(ni_b - ni_a, 2),
        "combined_mtr": round((it_b - it_a) + (ni_b - ni_a), 2),
    }


# ---------------------------------------------------------------------------
# Drift comparison
# ---------------------------------------------------------------------------

def within_tolerance(ours: float, published: float, pct: float = DRIFT_TOLERANCE_PCT) -> bool:
    if published == 0:
        return ours == 0
    return abs(ours - published) / abs(published) * 100 <= pct


def drift_pct(ours: float, published: float) -> float:
    if published == 0:
        return float("inf") if ours != 0 else 0.0
    return (ours - published) / abs(published) * 100


# ---------------------------------------------------------------------------
# Per-scenario builders. Each returns (fixture_dict, drift_report_lines).
# ---------------------------------------------------------------------------

def build_b1() -> tuple[dict[str, Any], list[str]]:
    """B1 — PA raise £12,570 → £15,000, UK 2025. No blog reference; the local
    computation IS the fixture."""
    print("  fetching/preparing reform...")
    # B1 isn't a Vahid scenario, so we author the reform JSON inline. Small,
    # single-parameter, no risk of drift since the parameter isn't in baseline.
    reform = {
        "gov.hmrc.income_tax.allowances.personal_allowance.amount": {
            "2025-01-01.2025-12-31": 15000,
        },
    }
    print("  running locally...")
    t = time.time()
    result = run_economy(reform=reform, baseline=None, time_period=2025)
    print(f"    ({time.time()-t:.0f}s)")

    fixture = {
        "_source": "local policyengine 0.13.0 + policyengine_uk 2.88.20",
        "_scenario": "PA £12,570 → £15,000, UK 2025, EFRS 2023-24",
        "budget": result["budget"],
        "decile": result["decile"],
        "poverty": result["poverty"],
        "inequality": result["inequality"],
    }
    drift = [
        "## b1_society_wide_pa",
        "",
        "No published reference (B1 is an author-defined scenario, not from a blog post).",
        "Local computation is the canonical fixture.",
        "",
        f"- budgetary_impact: £{result['budget']['budgetary_impact']:+,.0f}",
        f"- tax_revenue_impact: £{result['budget']['tax_revenue_impact']:+,.0f}",
        f"- benefit_spending_impact: £{result['budget']['benefit_spending_impact']:+,.0f}",
    ]
    return fixture, drift


def build_b2() -> tuple[dict[str, Any], list[str]]:
    """B2 — stacked NI/IT/freeze (Vahid Nov-2025 post). Filter against
    published figures per the drift threshold."""
    print("  fetching reform JSONs from PE-API...")
    reforms = {
        "freeze":   fetch_reform_json(83092),
        "ni_alone": fetch_reform_json(94906),
        "it_alone": fetch_reform_json(94910),
        "ni_layer": fetch_reform_json(94938),
        "combined": fetch_reform_json(94911),
    }
    for name, rj in reforms.items():
        print(f"    {name}: {len(rj)} parameter(s)")

    print("  running scenarios...")
    runs = {}
    t = time.time()
    runs["freeze"]   = run_economy(reform=reforms["freeze"],   baseline=None,              time_period=2028)
    print(f"    freeze done ({time.time()-t:.0f}s)"); t = time.time()
    runs["ni_alone"] = run_economy(reform=reforms["ni_alone"], baseline=None,              time_period=2026)
    print(f"    ni_alone done ({time.time()-t:.0f}s)"); t = time.time()
    runs["it_alone"] = run_economy(reform=reforms["it_alone"], baseline=None,              time_period=2026)
    print(f"    it_alone done ({time.time()-t:.0f}s)"); t = time.time()
    runs["ni_layer"] = run_economy(reform=reforms["ni_layer"], baseline=reforms["freeze"], time_period=2026)
    print(f"    ni_layer done ({time.time()-t:.0f}s)"); t = time.time()
    runs["it_layer"] = run_economy(reform=reforms["combined"], baseline=reforms["ni_layer"], time_period=2026)
    print(f"    it_layer done ({time.time()-t:.0f}s)"); t = time.time()
    runs["combined"] = run_economy(reform=reforms["combined"], baseline=None,              time_period=2026)
    print(f"    combined done ({time.time()-t:.0f}s)")

    # Vahid's published figures (uk-income-tax-ni-reforms-2025.md, Nov 2025).
    PUBLISHED = {
        "freeze_layer.budgetary_impact":      3_500_000_000,   # £3.5bn in 2028-29
        "ni_layer.budgetary_impact":        -11_700_000_000,   # -£11.7bn in 2026-27
        "it_layer.budgetary_impact":         18_600_000_000,   # +£18.6bn in 2026-27
        "combined.budgetary_impact":          6_900_000_000,   # +£6.9bn in 2026-27
    }
    OURS = {
        "freeze_layer.budgetary_impact":   runs["freeze"]["budget"]["budgetary_impact"],
        "ni_layer.budgetary_impact":       runs["ni_layer"]["budget"]["budgetary_impact"],
        "it_layer.budgetary_impact":       runs["it_layer"]["budget"]["budgetary_impact"],
        "combined.budgetary_impact":       runs["combined"]["budget"]["budgetary_impact"],
    }

    drift = ["## b2_ni_it_stacked", ""]
    drift.append(f"Drift threshold: {DRIFT_TOLERANCE_PCT}%")
    drift.append("")
    drift.append("| field | published | ours | drift | kept? |")
    drift.append("|---|---|---|---|---|")
    fixture: dict[str, Any] = {
        "_source": "local policyengine 0.13.0 + policyengine_uk 2.88.20",
        "_scenario": "Reeves Nov-2025 NI/IT/freeze package (Vahid blog uk-income-tax-ni-reforms-2025.md)",
        "_published": "https://policyengine.org/uk/research/uk-income-tax-ni-reforms-2025",
    }
    for key, pub in PUBLISHED.items():
        ours = OURS[key]
        d = drift_pct(ours, pub)
        kept = within_tolerance(ours, pub)
        drift.append(f"| `{key}` | £{pub:+,.0f} | £{ours:+,.0f} | {d:+.1f}% | {'✓' if kept else '✗'} |")
        if kept:
            section, field = key.split(".")
            fixture.setdefault(section, {})[field] = ours
    drift.append("")

    # Per-decile patterns: NI cut alone + IT increase alone reproduced cleanly
    # in our trial. Save those distributions to the fixture. Freeze distribution
    # is dropped because the layer itself drops out under drift.
    fixture["ni_alone"] = {"decile_relative": runs["ni_alone"]["decile"]["relative"]}
    fixture["it_alone"] = {"decile_relative": runs["it_alone"]["decile"]["relative"]}

    drift.append("Per-decile distributions saved for ni_alone and it_alone")
    drift.append("(freeze_layer + combined distributions dropped due to baseline drift).")
    return fixture, drift


def build_b3() -> tuple[dict[str, Any], list[str]]:
    """B3 — household calc, deterministic. No blog reference. Local = fixture."""
    print("  running single-household calculation...")
    situation = {
        "people": {"p": {"age": 35, "employment_income": 45000}},
        "benunits": {"b": {"members": ["p"]}},
        "households": {"h": {"members": ["p"]}},
    }
    base = run_household(situation, 2025)
    # MTR by finite difference
    bumped = {
        "people": {"p": {"age": 35, "employment_income": 45100}},
        "benunits": {"b": {"members": ["p"]}},
        "households": {"h": {"members": ["p"]}},
    }
    bumped_net = run_household(bumped, 2025)
    mtr = (1 - (bumped_net["household_net_income"] - base["household_net_income"]) / 100) * 100

    fixture = {
        "_source": "local policyengine_uk 2.88.20",
        "_scenario": "single adult age 35, gross £45,000, UK 2025, no microdata",
        "result": {**base, "marginal_tax_rate": mtr},
    }
    drift = [
        "## b3_household_calc",
        "",
        "No published reference. Local computation is the canonical fixture.",
        "",
        f"- household_net_income: £{base['household_net_income']:,.2f}",
        f"- income_tax:           £{base['income_tax']:,.2f}",
        f"- national_insurance:   £{base['national_insurance']:,.2f}",
        f"- marginal_tax_rate:    {mtr:.2f}%",
    ]
    return fixture, drift


def build_b4() -> tuple[dict[str, Any], list[str]]:
    """B4 — MTR schedule at 8 income points, local-computed."""
    print("  computing MTR schedule (8 income points)...")
    rows = [mtr_at(2025, inc) for inc in (10000, 20000, 30000, 50000, 75000, 100000, 125000, 150000)]
    fixture = {
        "_source": "local policyengine_uk 2.88.20",
        "_scenario": "single adult MTR schedule, UK 2025, finite-difference",
        "rows": rows,
    }
    drift = [
        "## b4_mtr_schedule",
        "",
        "No published reference. Local computation is the canonical fixture.",
        "",
        "Combined IT+NI MTR by gross income:",
    ]
    drift.extend(f"  £{r['gross']:>7,}: {r['combined_mtr']:5.1f}%" for r in rows)
    return fixture, drift


def build_b5_dropped() -> tuple[dict[str, Any] | None, list[str]]:
    """B5 — two-child limit removal. Dropped: the reform is a no-op against
    current policyengine_uk 2.88.20 (which incorporates the Autumn Budget 2025
    removal as baseline). Documented here for the drift report."""
    print("  (scenario marked dropped — no-op vs current baseline)")
    return None, [
        "## b5_two_child_limit",
        "",
        "**Dropped — model baseline drift.**",
        "",
        "Vahid's reform 93219 sets `child_count` cap to 100/102 effective 2025+,",
        "which was meaningful when the post was published (pre-Autumn Budget 2025).",
        "policyengine_uk 2.88.20 now has the cap at `inf` from 2026 onward as",
        "current law (the Autumn Budget 2025 removal is baked into baseline).",
        "",
        "Result: reform vs current law is a zero-delta no-op. £0 budgetary impact,",
        "0pp poverty change, 0% Gini change.",
        "",
        "Re-enabling requires either (a) replacing with a different reform Vahid",
        "wrote that is still counterfactual today, or (b) pinning to an older",
        "policyengine_uk version that pre-dates the baseline update.",
    ]


# ---------------------------------------------------------------------------
# Validation (read-only) path
# ---------------------------------------------------------------------------

def resolve_path(node: Any, dotted_path: str) -> tuple[bool, Any]:
    cur = node
    for part in dotted_path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit():
            idx = int(part)
            if 0 <= idx < len(cur):
                cur = cur[idx]
            else:
                return False, None
        else:
            return False, None
    return True, cur


def validate_fixture_paths(scenario_id: str, scenario: dict[str, Any], fixture: dict[str, Any]) -> list[str]:
    ref = scenario.get("reference") or {}
    misses = []
    for fc in ref.get("fields_to_compare") or []:
        path = fc["path"]
        if fc.get("expected_approx") is not None:
            continue
        ok, value = resolve_path(fixture, path)
        if not ok:
            misses.append(path)
    return misses


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

BUILDERS = {
    "b1": ("b1_society_wide_pa.json",     build_b1),
    "b2": ("b2_ni_it_stacked.json",       build_b2),
    "b3": ("b3_household_calc.json",      build_b3),
    "b4": ("b4_mtr_schedule.json",        build_b4),
    "b5": ("b5_two_child_limit.json",     build_b5_dropped),
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenarios", nargs="*",
                        help="Scenario shorthand (b1, b2, ...). Empty = all.")
    parser.add_argument("--validate-only", action="store_true",
                        help="Don't rebuild; check scenario YAML paths resolve in existing fixtures.")
    args = parser.parse_args()

    keys = args.scenarios or sorted(BUILDERS.keys())
    bad = [k for k in keys if k not in BUILDERS]
    if bad:
        raise SystemExit(f"Unknown scenarios: {bad}. Known: {sorted(BUILDERS)}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    # Surface HF token so policyengine_core can download the dataset.
    if not os.environ.get("HUGGING_FACE_TOKEN"):
        env_file = Path(__file__).resolve().parents[3] / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith("POLICYENGINE_UK_DATA_TOKEN="):
                    os.environ["HUGGING_FACE_TOKEN"] = line.split("=", 1)[1].strip()
                    break

    drift_report = [
        "# Test B fixture drift report",
        "",
        f"Generated by `build_fixtures.py`. Drift threshold: {DRIFT_TOLERANCE_PCT}%.",
        "",
        "For each scenario, fields whose locally-computed value drifted more than",
        "the threshold from the published reference are dropped from the fixture.",
        "Dropped fields indicate `policyengine_uk` baseline has moved since the",
        "post was published — not a bug, just model evolution.",
        "",
        "---",
        "",
    ]

    all_misses: list[tuple[str, list[str]]] = []
    for key in keys:
        filename, builder = BUILDERS[key]
        fixture_path = FIXTURES_DIR / filename
        print(f"\n=== {key} → {filename} ===")

        if args.validate_only:
            if not fixture_path.exists():
                print(f"  no fixture at {fixture_path} (skipping)")
                continue
            fixture = json.loads(fixture_path.read_text())
        else:
            fixture, drift = builder()
            drift_report.extend(drift)
            drift_report.append("")
            if fixture is None:
                # Dropped scenario — remove any stale fixture file
                if fixture_path.exists():
                    fixture_path.unlink()
                    print(f"  removed stale fixture {fixture_path}")
                continue
            fixture_path.write_text(json.dumps(fixture, indent=2, default=str))
            print(f"  wrote {fixture_path}")

        # Validate the scenario YAML paths resolve in this fixture
        scenario_files = list(SCENARIOS_DIR.glob(f"{key}_*.yaml"))
        if scenario_files:
            scenario = yaml.safe_load(scenario_files[0].read_text())
            misses = validate_fixture_paths(scenario["id"], scenario, fixture)
            if misses:
                all_misses.append((scenario["id"], misses))
                print(f"  ⚠ {len(misses)} field path(s) didn't resolve in fixture:")
                for m in misses:
                    print(f"    - {m}")
            else:
                print(f"  ✓ all field paths resolve")

    if not args.validate_only:
        drift_path = FIXTURES_DIR.parent / "drift_report.md"
        drift_path.write_text("\n".join(drift_report))
        print(f"\nwrote {drift_path}")

    if all_misses:
        print("\n=== validation: ISSUES ===")
        for sid, misses in all_misses:
            print(f"  {sid}: {misses}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
