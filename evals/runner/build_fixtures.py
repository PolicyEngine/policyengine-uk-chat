#!/usr/bin/env python3
"""
Build reference fixtures for Test B scenarios.

Most fixtures come from live PE-API calls (api.policyengine.org). B4 (MTR
schedule) is computed locally via the policyengine_uk package since the API
has no MTR endpoint. B2 (stacked NI/IT/freeze) is assembled from multiple
PE-API calls because the YAML's fixture shape has per-layer keys that no
single API response produces.

Run on demand — generated fixtures are committed to git so the grader
doesn't have to refetch on every CI run.

Usage:
    python evals/runner/build_fixtures.py                    # all scenarios
    python evals/runner/build_fixtures.py b1 b3              # just these
    python evals/runner/build_fixtures.py --validate-only    # don't refetch,
                                                              # just check that
                                                              # each path in
                                                              # scenario YAMLs
                                                              # resolves in the
                                                              # existing fixture
"""

from __future__ import annotations

import argparse
import json
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
POLL_INTERVAL_SECONDS = 15
POLL_TIMEOUT_SECONDS = 600


# ---------------------------------------------------------------------------
# PE-API helpers
# ---------------------------------------------------------------------------

def create_policy(client: httpx.Client, country: str, data: dict[str, Any]) -> int:
    """POST a reform policy spec; return the new policy_id."""
    r = client.post(f"{PE_API_BASE}/{country}/policy", json={"data": data})
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"policy create failed: {body}")
    return body["result"]["policy_id"]


def poll_economy(
    client: httpx.Client,
    country: str,
    reform_id: int,
    baseline_id: int,
    region: str,
    time_period: str,
    dataset: str | None = None,
) -> dict[str, Any]:
    """Fire an economy-wide comparison and poll until status=ok."""
    params = {"region": region, "time_period": time_period}
    if dataset:
        params["dataset"] = dataset
    url = f"{PE_API_BASE}/{country}/economy/{reform_id}/over/{baseline_id}"

    print(f"  polling {url} (region={region}, time_period={time_period})...")
    started = time.time()
    while True:
        r = client.get(url, params=params)
        r.raise_for_status()
        body = r.json()
        status = body.get("status")
        if status == "ok":
            print(f"    done ({int(time.time() - started)}s)")
            return body["result"]
        if status == "error":
            raise RuntimeError(f"economy comparison errored: {body}")
        if time.time() - started > POLL_TIMEOUT_SECONDS:
            raise TimeoutError(
                f"economy comparison did not finish in {POLL_TIMEOUT_SECONDS}s"
            )
        time.sleep(POLL_INTERVAL_SECONDS)


def fetch_household(
    client: httpx.Client,
    country: str,
    household_id: int,
    policy_id: int,
) -> dict[str, Any]:
    r = client.get(f"{PE_API_BASE}/{country}/household/{household_id}/policy/{policy_id}")
    r.raise_for_status()
    body = r.json()
    if body.get("status") != "ok":
        raise RuntimeError(f"household fetch failed: {body}")
    return body["result"]


# ---------------------------------------------------------------------------
# Per-scenario fixture builders
# ---------------------------------------------------------------------------

def build_b1(client: httpx.Client) -> dict[str, Any]:
    """B1 — single-parameter PA raise from £12,570 to £15,000, UK 2025."""
    reform_data = {
        "gov.hmrc.income_tax.allowances.personal_allowance.amount": {
            "2025-01-01.2025-12-31": 15000,
        }
    }
    reform_id = create_policy(client, "uk", reform_data)
    print(f"  created reform policy {reform_id}")
    return poll_economy(
        client,
        country="uk",
        reform_id=reform_id,
        baseline_id=1,
        region="uk",
        time_period="2025",
    )


def build_b2(client: httpx.Client) -> dict[str, Any]:
    """B2 — stacked NI/IT/freeze.

    The YAML's reference shape has per-layer keys (`layers.freeze_extension.*`,
    `layers.ni_cut.*`, `layers.it_increase.*`). We fetch the four reform IDs
    Vahid cites in uk-income-tax-ni-reforms-2025.md and assemble.

    Vahid's reform IDs (from the post):
      83092 — freeze extension to 2029-30 (vs current law baseline 1)
      94938 — NI cut applied on top of freeze (baseline 83092)
      94911 — IT increase on top of freeze + NI cut (baseline 94938)
      94906 — NI cut alone vs current law (for the standalone decile chart)
      94910 — IT increase alone vs current law (for the standalone decile chart)
    """
    print("  fetching freeze extension impact (reform 83092, 2028-29)...")
    freeze_2028 = poll_economy(
        client, "uk", reform_id=83092, baseline_id=1,
        region="uk", time_period="2028",
    )

    print("  fetching NI cut layer (reform 94938 over 83092, 2026-27)...")
    ni_layer = poll_economy(
        client, "uk", reform_id=94938, baseline_id=83092,
        region="uk", time_period="2026",
    )

    print("  fetching IT increase layer (reform 94911 over 94938, 2026-27)...")
    it_layer = poll_economy(
        client, "uk", reform_id=94911, baseline_id=94938,
        region="uk", time_period="2026",
    )

    print("  fetching combined impact (reform 94911 over 1, 2026-27)...")
    combined = poll_economy(
        client, "uk", reform_id=94911, baseline_id=1,
        region="uk", time_period="2026",
    )

    print("  fetching NI cut alone (reform 94906 over 1, 2026-27)...")
    ni_alone = poll_economy(
        client, "uk", reform_id=94906, baseline_id=1,
        region="uk", time_period="2026",
    )

    print("  fetching IT increase alone (reform 94910 over 1, 2026-27)...")
    it_alone = poll_economy(
        client, "uk", reform_id=94910, baseline_id=1,
        region="uk", time_period="2026",
    )

    return {
        "combined": {
            "budgetary_impact_2026_27": combined.get("budget", {}).get("budgetary_impact"),
            "_raw": combined,
        },
        "layers": {
            "freeze_extension": {
                "budgetary_impact_2028_29": freeze_2028.get("budget", {}).get("budgetary_impact"),
                "_raw": freeze_2028,
            },
            "ni_cut": {
                "budgetary_impact_2026_27": ni_layer.get("budget", {}).get("budgetary_impact"),
                "_raw": ni_layer,
            },
            "it_increase": {
                "budgetary_impact_2026_27": it_layer.get("budget", {}).get("budgetary_impact"),
                "_raw": it_layer,
            },
        },
        "decile": {
            "relative": {
                "ni_cut": ni_alone.get("decile", {}).get("relative"),
                "it_increase": it_alone.get("decile", {}).get("relative"),
            },
        },
        # example_household figures come from Vahid's hand-computed table in the
        # post (£60k earner + £10k pension). They aren't an API endpoint — they
        # are the canonical illustrative-household example from the post.
        "example_household": {
            "net_change": 5.4,
            "ni_change": -754.0,
            "it_change": 748.6,
        },
    }


def build_b3() -> dict[str, Any]:
    """B3 — household calc, computed locally via policyengine_uk.

    The PE-API household endpoint requires a stored household_id, which means
    we'd have to POST a household spec first. Easier to call the package
    directly — same engine, no policy or household round-trip.
    """
    print("  computing household via policyengine_uk locally...")
    from policyengine_uk import Simulation

    situation = {
        "people": {"p": {"age": 35, "employment_income": 45000}},
        "benunits": {"b": {"members": ["p"]}},
        "households": {"h": {"members": ["p"]}},
    }
    sim = Simulation(situation=situation)
    net = float(sim.calculate("household_net_income", 2025)[0])
    income_tax = float(sim.calculate("income_tax", 2025)[0])
    ni = float(sim.calculate("national_insurance", 2025)[0])

    # MTR by finite difference (+£100 of employment income)
    bumped = {
        "people": {"p": {"age": 35, "employment_income": 45100}},
        "benunits": {"b": {"members": ["p"]}},
        "households": {"h": {"members": ["p"]}},
    }
    sim_b = Simulation(situation=bumped)
    net_b = float(sim_b.calculate("household_net_income", 2025)[0])
    mtr = (1 - (net_b - net) / 100) * 100

    return {
        "result": {
            "household_net_income": net,
            "income_tax": income_tax,
            "national_insurance": ni,
            "marginal_tax_rate": mtr,
        }
    }


def build_b4() -> dict[str, Any]:
    """B4 — MTR schedule at 8 income points, computed locally."""
    print("  computing MTR schedule via policyengine_uk locally...")
    from policyengine_uk import Simulation

    incomes = [10000, 20000, 30000, 50000, 75000, 100000, 125000, 150000]

    def sit(income: int) -> dict[str, Any]:
        return {
            "people": {"p": {"age": 35, "employment_income": income}},
            "benunits": {"b": {"members": ["p"]}},
            "households": {"h": {"members": ["p"]}},
        }

    def at(income: int) -> dict[str, float]:
        s = Simulation(situation=sit(income))
        return {
            "it": float(s.calculate("income_tax", 2025)[0]),
            "ni": float(s.calculate("national_insurance", 2025)[0]),
        }

    rows = []
    for income in incomes:
        a = at(income)
        b = at(income + 100)
        it_mtr = (b["it"] - a["it"])  # change in £100 = pp directly
        ni_mtr = (b["ni"] - a["ni"])
        rows.append({
            "gross": income,
            "it_mtr": round(it_mtr, 2),
            "ni_mtr": round(ni_mtr, 2),
            "combined_mtr": round(it_mtr + ni_mtr, 2),
        })

    return {"rows": rows}


def build_b5(client: httpx.Client) -> dict[str, Any]:
    """B5 — remove the two-child limit (Vahid's reform 93219, 2026-27)."""
    print("  fetching reform 93219 over 1, region=uk, 2026...")
    result = poll_economy(
        client, "uk", reform_id=93219, baseline_id=1,
        region="uk", time_period="2026",
    )
    return result


# ---------------------------------------------------------------------------
# Validation: every fields_to_compare.path resolves in the fixture
# ---------------------------------------------------------------------------

def resolve_path(node: Any, dotted_path: str) -> tuple[bool, Any]:
    """Return (resolved, value). Integer-looking parts index into lists."""
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
    """For every fields_to_compare.path, check it resolves in the fixture.

    Returns a list of paths that didn't resolve.
    """
    ref = scenario.get("reference") or {}
    misses = []
    for fc in ref.get("fields_to_compare") or []:
        path = fc["path"]
        if fc.get("expected_approx") is not None:
            # Has an inline expected value, no fixture lookup required.
            continue
        ok, value = resolve_path(fixture, path)
        if not ok:
            misses.append(path)
        elif not isinstance(value, (int, float)):
            misses.append(f"{path} (resolved but value is {type(value).__name__}, expected number)")
    return misses


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

BUILDERS = {
    "b1": ("b1_society_wide_pa.json", lambda c: build_b1(c)),
    "b2": ("b2_ni_it_stacked.json", lambda c: build_b2(c)),
    "b3": ("b3_household_calc.json", lambda c: build_b3()),
    "b4": ("b4_mtr_schedule.json", lambda c: build_b4()),
    "b5": ("b5_two_child_limit.json", lambda c: build_b5(c)),
}


def load_scenario(scenario_id_prefix: str) -> dict[str, Any]:
    """Load the scenario YAML matching b1, b2, ... shorthand."""
    matches = list(SCENARIOS_DIR.glob(f"{scenario_id_prefix}_*.yaml"))
    if not matches:
        raise SystemExit(f"No scenario YAML matching '{scenario_id_prefix}_*'")
    if len(matches) > 1:
        raise SystemExit(f"Multiple matches: {[m.name for m in matches]}")
    return yaml.safe_load(matches[0].read_text())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenarios", nargs="*",
        help="Scenario shorthand (b1, b2, ...). Empty = all B scenarios.",
    )
    parser.add_argument(
        "--validate-only", action="store_true",
        help="Skip rebuilding fixtures; just validate that scenario field paths "
             "resolve in the existing fixture JSONs.",
    )
    args = parser.parse_args()

    keys = args.scenarios or sorted(BUILDERS.keys())
    bad = [k for k in keys if k not in BUILDERS]
    if bad:
        raise SystemExit(f"Unknown scenarios: {bad}. Known: {sorted(BUILDERS)}")

    FIXTURES_DIR.mkdir(parents=True, exist_ok=True)

    overall_misses: list[tuple[str, list[str]]] = []

    with httpx.Client(timeout=60.0) as client:
        for key in keys:
            filename, builder = BUILDERS[key]
            fixture_path = FIXTURES_DIR / filename
            scenario = load_scenario(key)
            print(f"\n=== {key} → {filename} ===")

            if args.validate_only:
                if not fixture_path.exists():
                    print(f"  no fixture at {fixture_path} (skipping)")
                    continue
                fixture = json.loads(fixture_path.read_text())
            else:
                if key in ("b3", "b4"):
                    fixture = builder(None)  # local computation, no httpx client needed
                else:
                    fixture = builder(client)
                fixture_path.write_text(json.dumps(fixture, indent=2, default=str))
                print(f"  wrote {fixture_path}")

            misses = validate_fixture_paths(scenario["id"], scenario, fixture)
            if misses:
                overall_misses.append((scenario["id"], misses))
                print(f"  ⚠ {len(misses)} field path(s) didn't resolve in fixture:")
                for path in misses:
                    print(f"    - {path}")
            else:
                print(f"  ✓ all field paths resolve")

    if overall_misses:
        print("\n=== validation: ISSUES ===")
        for sid, misses in overall_misses:
            print(f"  {sid}: {misses}")
        print("\nFix either the fixture (rename keys), the scenario YAML "
              "(rename paths), or both. The grader silently skips unresolved "
              "paths, so these would otherwise hide as 'unextracted'.")
        return 1

    print("\n✓ all done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
