"""CLI entrypoint for evaluations against a deployed UK Chat backend."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

from eval.deployed_runner import run_deployed_eval


DEFAULT_CASE_FILE = Path("evals/cases/tool_loop/uk_population_live.yaml")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deployed UK Chat evals")
    parser.add_argument(
        "--backend-url",
        default=os.environ.get("EVAL_BACKEND_URL"),
        help="UK Chat backend URL; defaults to EVAL_BACKEND_URL",
    )
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument("--case-id", default=None)
    parser.add_argument("--trial-timeout-seconds", type=float, default=600)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--report-dir", type=Path, default=None)
    parser.add_argument("--no-report", action="store_true")
    args = parser.parse_args()
    if not args.backend_url:
        parser.error("--backend-url or EVAL_BACKEND_URL is required")
    if not os.environ.get("EVAL_RUN_TOKEN"):
        parser.error("EVAL_RUN_TOKEN is required")
    return args


def main() -> int:
    args = parse_args()
    report = asyncio.run(
        run_deployed_eval(
            case_file=args.case_file,
            case_id=args.case_id,
            backend_url=args.backend_url,
            token=os.environ["EVAL_RUN_TOKEN"],
            timeout_seconds=args.trial_timeout_seconds,
            concurrency=args.concurrency,
            report_dir=args.report_dir,
            write_reports=not args.no_report,
        )
    )
    print(
        f"Deployed AI evals: {report.passed} passed, {report.failed} failed, "
        f"{report.skipped} skipped"
    )
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
