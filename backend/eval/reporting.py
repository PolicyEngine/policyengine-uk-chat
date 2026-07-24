"""Report writing for AI evaluations."""

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
    model_metrics = []
    if report.trial_count:
        model_metrics = [
            f"- Model trials per case: `{report.trial_count}`",
            f"- Model pass@1: `{report.pass_at_1:.1%}`",
            f"- Model pass^{report.trial_count}: `{report.pass_all_trials:.1%}`",
        ]
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
        *model_metrics,
        "",
        "| Suite | Case | Trial | Model | Status | Score | Notes |",
        "| --- | --- | ---: | --- | --- | ---: | --- |",
    ]
    for result in report.results:
        details = result.model_dump(mode="json")["details"]
        notes = "; ".join(result.errors) if result.errors else json.dumps(details, sort_keys=True)
        notes = notes.replace("\n", " ")[:500]
        lines.append(
            f"| {result.suite} | `{result.id}` | {result.trial} | "
            f"`{result.model or 'n/a'}` | {result.status} | "
            f"{result.score:.2f} | {notes} |"
        )
    lines.append("")
    return "\n".join(lines)
