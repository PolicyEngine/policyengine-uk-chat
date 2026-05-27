#!/usr/bin/env python3
"""Per-scenario tool-routing table for a finished eval run.

Reads `runs/<timestamp>/manifest.json` and prints which tool Claude called
how many times on each scenario. Useful for A/B-comparing tool-surface
changes: e.g. did registering `calculate_household` actually shift
household-shaped questions away from `run_python`?

Usage:
    python evals/runner/tool_usage.py runs/2026-05-27_120000
    python evals/runner/tool_usage.py runs/<ts1> runs/<ts2>  # diff two runs
"""

from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path


def load_run(run_dir: Path) -> dict[str, Counter]:
    manifest = json.loads((run_dir / "manifest.json").read_text())
    by_scenario: dict[str, Counter] = {}
    for r in manifest["runs"]:
        c = by_scenario.setdefault(r["scenario_id"], Counter())
        c.update(r.get("tool_call_counts_by_name") or {})
    return by_scenario


def print_table(by_scenario: dict[str, Counter], label: str) -> None:
    all_tools = sorted({t for c in by_scenario.values() for t in c})
    if not all_tools:
        print(f"{label}: no tool calls recorded")
        return
    width = max(22, max(len(t) for t in all_tools) + 2)
    header = f"{'scenario':30}" + "".join(f"  {t:{width}}" for t in all_tools)
    print(f"\n=== {label} ===")
    print(header)
    print("-" * len(header))
    for sid in sorted(by_scenario):
        row = f"{sid:30}" + "".join(
            f"  {by_scenario[sid][t]:{width}}" for t in all_tools
        )
        print(row)


def main(argv: list[str]) -> int:
    if not argv:
        print(__doc__)
        return 1
    for path_str in argv:
        run_dir = Path(path_str)
        if not (run_dir / "manifest.json").exists():
            print(f"skip {run_dir}: no manifest.json", file=sys.stderr)
            continue
        print_table(load_run(run_dir), label=str(run_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
