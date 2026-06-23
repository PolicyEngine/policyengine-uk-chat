"""Report writing for manual AI evaluations."""

import json
from pathlib import Path

from eval.schemas import EvalReport


def _format_ms(value: float) -> str:
    if value >= 1000:
        return f"{value / 1000:.2f}s"
    return f"{value:.1f}ms"


def write_report(report: EvalReport, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = report.run_id or f"{report.started_at.replace(':', '').replace('-', '').replace('+', '')}-{report.mode}"
    if report.run_id is None:
        report = report.model_copy(update={"run_id": stem})
    json_path = report_dir / f"{stem}.json"
    markdown_path = report_dir / f"{stem}.md"

    json_path.write_text(report.model_dump_json(indent=2))
    markdown_path.write_text(render_markdown(report))
    return json_path, markdown_path


def render_markdown(report: EvalReport) -> str:
    lines = [
        "# UK Chat AI Eval Report",
        "",
        f"- Mode: `{report.mode}`",
        f"- Provider: `{report.provider}`",
        f"- Model: `{report.model or 'n/a'}`",
        f"- Git SHA: `{report.git_sha or 'unknown'}`",
        f"- Run ID: `{report.run_id or 'unknown'}`",
        f"- Suites: `{', '.join(report.suites)}`",
        f"- Passed: `{report.passed}`",
        f"- Failed: `{report.failed}`",
        f"- Skipped: `{report.skipped}`",
        f"- Duration: `{_format_ms(report.duration_ms)}`",
        "",
    ]
    if report.timing_summary:
        lines.extend(
            [
                "## Timing Summary",
                "",
                "| Suite | Cases | Total | Avg | P50 | P95 | Max |",
                "| --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for suite, timing in report.timing_summary.items():
            lines.append(
                f"| {suite} | {timing.count} | {_format_ms(timing.total_ms)} | "
                f"{_format_ms(timing.avg_ms)} | {_format_ms(timing.p50_ms)} | "
                f"{_format_ms(timing.p95_ms)} | {_format_ms(timing.max_ms)} |"
            )
        lines.append("")
    lines.extend(
        [
            "## Cases",
            "",
            "| Suite | Case | Status | Score | Duration | Notes |",
            "| --- | --- | --- | ---: | ---: | --- |",
        ]
    )
    for result in report.results:
        notes = "; ".join(result.errors) if result.errors else json.dumps(result.details, sort_keys=True)
        notes = notes.replace("\n", " ")[:500]
        lines.append(
            f"| {result.suite} | `{result.id}` | {result.status} | "
            f"{result.score:.2f} | {_format_ms(result.duration_ms)} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
