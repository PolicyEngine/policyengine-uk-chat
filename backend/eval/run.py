"""CLI entrypoint for manual UK chat AI evaluations."""

import argparse
import sys
from pathlib import Path

from eval.runner import SUITE_DIRS, run_eval


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run manual UK chat AI evals")
    parser.add_argument(
        "--suite",
        action="append",
        choices=["all", *SUITE_DIRS.keys()],
        default=None,
        help="Suite to run. Repeat for multiple suites. Defaults to all.",
    )
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--provider", choices=["anthropic"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--no-report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not args.suite or "all" in args.suite:
        suites = list(SUITE_DIRS)
    else:
        suites = args.suite

    report = run_eval(
        suites=suites,
        mode=args.mode,
        provider=args.provider,
        model=args.model,
        report_dir=args.report_dir,
        write_reports=not args.no_report,
    )
    print(
        f"AI evals: {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} skipped"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
