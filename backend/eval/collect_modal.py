"""Collect detached Modal case reports into the standard eval report format."""

import argparse
from pathlib import Path

from eval.modal_batch import (
    aggregate_case_reports,
    case_report_filename,
    load_deployed_case_ids,
)
from eval.reporting import write_report
from eval.schemas import EvalReport


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CASE_FILE = REPO_ROOT / "evals/cases/tool_loop/uk_population_live.yaml"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--case-report-dir", type=Path, required=True)
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=REPO_ROOT / "evals/reports",
    )
    args = parser.parse_args(argv)

    case_ids = load_deployed_case_ids(args.case_file)
    reports = [
        EvalReport.model_validate_json(
            (args.case_report_dir / case_report_filename(case_id)).read_text()
        )
        for case_id in case_ids
    ]
    report = aggregate_case_reports(reports, case_ids)
    json_path, markdown_path = write_report(report, args.output_dir)
    print(
        f"Passed: {report.passed}; failed: {report.failed}; "
        f"skipped: {report.skipped}"
    )
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
