"""CLI entrypoint for UK chat AI evaluations."""

import argparse
import sys
from pathlib import Path

from eval.runner import SUITE_NAMES, run_eval


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run UK chat AI evals")
    parser.add_argument(
        "--suite",
        action="append",
        choices=["all", *SUITE_NAMES],
        default=None,
        help="Suite to run. Repeat for multiple suites. Defaults to all.",
    )
    parser.add_argument("--mode", choices=["offline", "live"], default="offline")
    parser.add_argument("--provider", choices=["anthropic"], default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument(
        "--trials",
        type=int,
        default=1,
        help="Independent trials per live model case.",
    )
    parser.add_argument(
        "--case",
        action="append",
        default=None,
        help="Run a specific case ID. Repeat to select multiple cases.",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=None,
        help="Run cases with any selected tag. Repeat to select multiple tags.",
    )
    parser.add_argument(
        "--strict-requirements",
        action="store_true",
        help="Fail, instead of skip, when a selected case cannot run.",
    )
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--no-report", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    suites = None if not args.suite or "all" in args.suite else args.suite

    report = run_eval(
        suites=suites,
        mode=args.mode,
        provider=args.provider,
        model=args.model,
        trials=args.trials,
        case_ids=args.case,
        tags=args.tag,
        strict_requirements=args.strict_requirements,
        report_dir=args.report_dir,
        write_reports=not args.no_report,
    )
    print(
        f"AI evals: {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} skipped"
    )
    if report.trial_count:
        print(
            f"Model stability: pass@1={report.pass_at_1:.1%}, "
            f"pass^{report.trial_count}={report.pass_all_trials:.1%}"
        )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
