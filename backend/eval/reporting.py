"""Report writing for manual AI evaluations."""

import json
from pathlib import Path

from eval.schemas import EvalReport


def write_report(report: EvalReport, report_dir: Path) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{report.started_at.replace(':', '').replace('-', '').replace('+', '')}-{report.mode}"
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
        f"- Suites: `{', '.join(report.suites)}`",
        f"- Passed: `{report.passed}`",
        f"- Failed: `{report.failed}`",
        f"- Skipped: `{report.skipped}`",
        "",
        "| Suite | Case | Status | Score | Notes |",
        "| --- | --- | --- | ---: | --- |",
    ]
    for result in report.results:
        details = result.model_dump(mode="json")["details"]
        notes = "; ".join(result.errors) if result.errors else json.dumps(details, sort_keys=True)
        notes = notes.replace("\n", " ")[:500]
        lines.append(
            f"| {result.suite} | `{result.id}` | {result.status} | {result.score:.2f} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
