#!/usr/bin/env python3
"""
Grader for a finished eval run.

Two paths:
- Test A scenarios → emit a markdown grading sheet (one section per response)
  with the prompt, the anchor (golden path), and the chat response side by
  side. A human fills in rubric scores 1-5. Lightweight automated anchor
  checks (must_mention / must_not_say substring scans) are pre-populated as
  grader hints — not authoritative.
- Test B scenarios → load the matching fixture under evals/fixtures/pe_api/,
  extract numerics from the chat response, diff against fixture per
  fields_to_compare with tolerance. Also runs the must_mention / must_not_say
  anchor checks.

After both paths, the script writes:
  <run_dir>/A_grading.md           ← human grading sheet (edit in place)
  <run_dir>/B_results.json         ← machine-readable B verdicts
  <run_dir>/B_results.md           ← human-readable B summary
  <run_dir>/threshold_check.md     ← regenerated after A_grading.md is filled

Usage:
    python evals/runner/grade.py <run_dir>                   # both A + B
    python evals/runner/grade.py <run_dir> --test A          # just A sheet
    python evals/runner/grade.py <run_dir> --test B          # just B diff
    python evals/runner/grade.py <run_dir> --threshold-check # after grading A
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from statistics import mean, stdev
from typing import Any

import yaml


# Paths
EVALS_DIR = Path(__file__).resolve().parent.parent
FIXTURES_DIR = EVALS_DIR / "fixtures" / "pe_api"

# Test A rubric dimensions — must match the rubric block in scenario YAMLs.
A_RUBRIC_DIMENSIONS = (
    "relevance",
    "methodology",
    "reasonableness",
    "consistency",
    "honesty",
)

# Test A thresholds from SPEC.md.
A_MEAN_THRESHOLD = 4.0
A_TRUST_KILLER_MIN = 2   # no response < 2 on Reasonableness or Honesty
A_TRUST_KILLERS = ("reasonableness", "honesty")
A_FABRICATION_RATE_LIMIT = 0.2  # at most 1 in 5 responses with fabricated figures

# Test B thresholds from SPEC.md.
B_FIELD_ACCURACY_THRESHOLD = 0.95   # ≥95% of fields within tolerance
B_SELF_CONSISTENCY_SD_LIMIT = 0.005  # SD < 0.5% of mean
B_METHODOLOGY_DRIFT_LIMIT = 1        # ≤ 1 of 5 scenarios
B_FAILURE_RATE_LIMIT = 0.10          # < 10% failure rate


# ---------------------------------------------------------------------------
# Loading & shared helpers
# ---------------------------------------------------------------------------

@dataclass
class RunResponse:
    scenario_id: str
    run_index: int
    answer_text: str
    meta: dict[str, Any]
    scenario: dict[str, Any]  # frozen YAML copy alongside the run


def load_run(run_dir: Path) -> list[RunResponse]:
    """Walk a run directory and load every response the runner produced."""
    responses = []
    for scenario_dir in sorted(p for p in run_dir.iterdir() if p.is_dir()):
        scenario_path = scenario_dir / "scenario.yaml"
        if not scenario_path.exists():
            continue
        scenario = yaml.safe_load(scenario_path.read_text())
        for txt_path in sorted(scenario_dir.glob("run-*.txt")):
            # filename like run-2.txt
            run_index = int(txt_path.stem.split("-")[1])
            answer = txt_path.read_text()
            meta_path = scenario_dir / f"run-{run_index}.meta.json"
            meta = json.loads(meta_path.read_text()) if meta_path.exists() else {}
            responses.append(RunResponse(
                scenario_id=scenario["id"],
                run_index=run_index,
                answer_text=answer,
                meta=meta,
                scenario=scenario,
            ))
    return responses


def anchor_check(answer: str, anchor: dict[str, Any] | None) -> dict[str, Any]:
    """Run the lightweight regex checks the anchor defines.

    These are grader hints, not authoritative scores. A human can override.
    Substrings are matched case-insensitively. £ symbols and digits are
    normalised so e.g. '£100,000' matches '100k' loosely (the digits part).
    """
    if not anchor:
        return {"checked": False, "must_mention": [], "must_not_say": []}

    norm_answer = answer.lower()

    def hit(phrase: str) -> bool:
        return phrase.lower() in norm_answer

    must_mention = [
        {"phrase": p, "found": hit(p)}
        for p in (anchor.get("must_mention") or [])
    ]
    must_not_say = [
        {"phrase": p, "found": hit(p)}
        for p in (anchor.get("must_not_say") or [])
    ]
    return {
        "checked": True,
        "must_mention": must_mention,
        "must_not_say": must_not_say,
        "must_mention_pass_rate": (
            sum(1 for m in must_mention if m["found"]) / len(must_mention)
            if must_mention else None
        ),
        "must_not_say_violations": [
            m["phrase"] for m in must_not_say if m["found"]
        ],
    }


# ---------------------------------------------------------------------------
# Test A — grading sheet generator
# ---------------------------------------------------------------------------

def render_anchor_hints(check: dict[str, Any]) -> str:
    """Render the regex check result as a short markdown block."""
    if not check.get("checked"):
        return "_(no anchor)_"
    lines = []
    rate = check.get("must_mention_pass_rate")
    if rate is not None:
        lines.append(f"`must_mention` substring matches: **{rate:.0%}**")
        for m in check["must_mention"]:
            mark = "✓" if m["found"] else "✗"
            lines.append(f"  - {mark} `{m['phrase']}`")
    violations = check.get("must_not_say_violations") or []
    if violations:
        lines.append(f"`must_not_say` **VIOLATIONS** ({len(violations)}):")
        for v in violations:
            lines.append(f"  - ✗ `{v}`")
    elif check.get("must_not_say"):
        lines.append(f"`must_not_say`: clean ✓")
    return "\n".join(lines)


def render_a_sheet(responses: list[RunResponse]) -> str:
    """Emit a markdown sheet for human grading of Test A responses."""
    a_responses = [r for r in responses if r.scenario["test"] == "A"]
    lines = [
        "# Test A — grading sheet",
        "",
        "Fill in 1-5 scores for each rubric dimension under every response.",
        "The `must_mention` / `must_not_say` lines are grader hints from "
        "automated substring scans — not authoritative. Use the ideal "
        "explanation as your reference for what a Vahid-quality answer "
        "looks like.",
        "",
        f"_{len(a_responses)} responses to grade._",
        "",
    ]
    for r in a_responses:
        anchor = r.scenario.get("anchor") or {}
        check = anchor_check(r.answer_text, anchor)
        rubric = r.scenario.get("rubric") or {}
        ideal = anchor.get("ideal_explanation") or anchor.get("ideal_finding") or "_(none)_"
        meta_summary = r.meta.get("summary", {})

        lines.extend([
            "---",
            f"## {r.scenario_id} — run {r.run_index}",
            "",
            f"**Title:** {r.scenario['title']}",
            f"**Tool calls:** {meta_summary.get('tool_call_count', '?')} · "
            f"**Elapsed:** {r.meta.get('elapsed_seconds', '?')}s · "
            f"**Errors:** {meta_summary.get('error_count', '?')}",
            "",
            "### Prompt (user message)",
            "```",
            r.scenario["prompt"].strip(),
            "```",
            "",
        ])
        if r.scenario.get("scenario_context"):
            lines.extend([
                "### Scenario context (system-prompt prefix)",
                "<details><summary>show</summary>",
                "",
                "```",
                r.scenario["scenario_context"].strip(),
                "```",
                "</details>",
                "",
            ])
        lines.extend([
            "### Golden path",
            "<details open><summary>anchor</summary>",
            "",
            render_anchor_hints(check),
            "",
            "**Ideal explanation:**",
            "",
            ideal,
            "</details>",
            "",
            "### Chat response",
            "<details open><summary>response (run-N.txt)</summary>",
            "",
            "> " + r.answer_text.replace("\n", "\n> "),
            "</details>",
            "",
            "### Scores (fill in 1-5)",
        ])
        for dim in A_RUBRIC_DIMENSIONS:
            criterion = rubric.get(dim, "")
            lines.append(f"- **{dim.title()}**: ⬜  _{criterion}_")
        lines.extend([
            "- **Fabricated a figure not derivable from a model run?** ⬜ yes / no",
            "",
            "### Notes",
            "_(optional)_",
            "",
        ])
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Test A — threshold check (run after grading sheet is filled in)
# ---------------------------------------------------------------------------

SCORE_RE = re.compile(
    r"^\s*-\s+\*\*(?P<dim>\w+)\*\*:\s*(?P<score>[1-5])",
    re.MULTILINE,
)
FAB_RE = re.compile(
    r"\*\*Fabricated a figure not derivable from a model run\?\*\*\s*"
    r"(?:⬜\s*)?\s*(?P<answer>yes|no)\b",
    re.IGNORECASE,
)
HEADER_RE = re.compile(r"^##\s+(?P<id>\S+)\s+—\s+run\s+(?P<n>\d+)", re.MULTILINE)


def parse_a_sheet(sheet_text: str) -> list[dict[str, Any]]:
    """Pull filled-in scores out of the grading markdown."""
    # Split by ## headers — each section is one response.
    sections = re.split(r"(?=^##\s+\S+\s+—\s+run\s+\d+)", sheet_text, flags=re.MULTILINE)
    parsed = []
    for sec in sections:
        m_header = HEADER_RE.search(sec)
        if not m_header:
            continue
        scores: dict[str, int] = {}
        for m in SCORE_RE.finditer(sec):
            dim = m.group("dim").lower()
            if dim in A_RUBRIC_DIMENSIONS:
                scores[dim] = int(m.group("score"))
        fab_m = FAB_RE.search(sec)
        fabricated = (fab_m.group("answer").lower() == "yes") if fab_m else None
        parsed.append({
            "scenario_id": m_header.group("id"),
            "run_index": int(m_header.group("n")),
            "scores": scores,
            "fabricated": fabricated,
        })
    return parsed


def a_threshold_check(graded: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply the SPEC.md Test A thresholds to filled scores."""
    fully_graded = [
        g for g in graded
        if all(d in g["scores"] for d in A_RUBRIC_DIMENSIONS)
    ]
    incomplete = [
        g for g in graded if g not in fully_graded
    ]

    all_scores: list[int] = []
    trust_killer_failures: list[dict[str, Any]] = []
    fabrication_count = 0
    fabrication_total = 0

    for g in fully_graded:
        for dim, score in g["scores"].items():
            all_scores.append(score)
            if dim in A_TRUST_KILLERS and score < A_TRUST_KILLER_MIN:
                trust_killer_failures.append({
                    "scenario_id": g["scenario_id"],
                    "run_index": g["run_index"],
                    "dimension": dim,
                    "score": score,
                })
        if g["fabricated"] is True:
            fabrication_count += 1
        if g["fabricated"] is not None:
            fabrication_total += 1

    mean_score = mean(all_scores) if all_scores else None
    fab_rate = (
        fabrication_count / fabrication_total
        if fabrication_total > 0 else None
    )

    passes = (
        mean_score is not None
        and mean_score >= A_MEAN_THRESHOLD
        and not trust_killer_failures
        and (fab_rate is None or fab_rate <= A_FABRICATION_RATE_LIMIT)
    )

    return {
        "mean_score": mean_score,
        "mean_threshold": A_MEAN_THRESHOLD,
        "trust_killer_failures": trust_killer_failures,
        "trust_killer_threshold": A_TRUST_KILLER_MIN,
        "fabrication_count": fabrication_count,
        "fabrication_total": fabrication_total,
        "fabrication_rate": fab_rate,
        "fabrication_rate_limit": A_FABRICATION_RATE_LIMIT,
        "fully_graded_count": len(fully_graded),
        "incomplete_count": len(incomplete),
        "passes": bool(passes),
    }


# ---------------------------------------------------------------------------
# Test B — numeric extraction + diff
# ---------------------------------------------------------------------------

NUMBER_RE = re.compile(
    r"(?P<sign>[-+])?\s*£?\s*"
    r"(?P<num>\d[\d,]*(?:\.\d+)?)"
    r"\s*(?P<unit>bn|billion|m|million|k|thousand|pp|%)?",
    re.IGNORECASE,
)


def parse_number_near(text: str, label_regex: str) -> float | None:
    """Find a labelled financial figure in prose.

    Best-effort regex extraction. For each label match, we scan up to ~200
    chars ahead and prefer numbers that have an explicit £ prefix and a
    bn/m unit — those are the figures the chat is reporting as results,
    rather than reform parameters that happened to appear nearby (£15,000,
    £100,000). Falls back to the first number found if no scaled value
    appears in range.
    """
    label_pat = re.compile(label_regex, re.IGNORECASE)
    scaled_re = re.compile(
        r"(?P<sign>[-+])?\s*£?\s*"
        r"(?P<num>\d[\d,]*(?:\.\d+)?)"
        r"\s*(?P<unit>bn|billion|m|million)\b",
        re.IGNORECASE,
    )

    def to_float(num_m: re.Match) -> float | None:
        raw = num_m.group("num").replace(",", "")
        try:
            n = float(raw)
        except ValueError:
            return None
        unit = (num_m.group("unit") or "").lower()
        if unit in ("bn", "billion"):
            n *= 1_000_000_000
        elif unit in ("m", "million"):
            n *= 1_000_000
        elif unit in ("k", "thousand"):
            n *= 1_000
        if num_m.group("sign") == "-":
            n = -n
        return n

    for label_m in label_pat.finditer(text):
        # Look in a wider window for a scaled (bn/m) figure first.
        tail = text[label_m.end():label_m.end() + 200]
        scaled_m = scaled_re.search(tail)
        if scaled_m:
            n = to_float(scaled_m)
            if n is not None:
                # If the surrounding prose suggests this is a decrease but the
                # number didn't carry a minus sign, flip it.
                surrounding = text[label_m.start():label_m.end() + scaled_m.end()].lower()
                if (
                    n > 0
                    and re.search(r"\b(reduc|cut|fall|decrease|cost|forg(o|on)e|less)", surrounding)
                    and not re.search(r"\bincrease|rais|gain|more\b", surrounding)
                ):
                    n = -n
                return n
        # Fall back to any number in the closer window.
        num_m = NUMBER_RE.search(text[label_m.end():label_m.end() + 120])
        if num_m:
            return to_float(num_m)
    return None


# Heuristic mapping from anchor field-path → label regex used to find the number
# in the chat's prose answer. Adding new B scenarios with new field paths needs
# either a matching label here or a per-scenario extractor.
FIELD_LABELS = {
    "budget.budgetary_impact": r"budgetary impact",
    "budget.tax_revenue_impact": r"(income\s*tax\s*revenue|tax\s*revenue\s*(impact|change))",
    "budget.benefit_spending_impact": r"benefit\s*spending",
    "combined.budgetary_impact_2026_27": r"combined.*(budgetary impact|revenue).*2026",
    "layers.freeze_extension.budgetary_impact_2028_29": r"(threshold\s*freeze|freeze\s*extension).*(2028|£3.5)",
    "layers.ni_cut.budgetary_impact_2026_27": r"(national\s*insurance|NI).*(cost|reduction|cut).*£",
    "layers.it_increase.budgetary_impact_2026_27": r"(income\s*tax|IT).*(increase|raise|rise).*£",
    "example_household.net_change": r"(£60[,]?000|example household|illustrative).*net",
    "example_household.ni_change": r"(£60[,]?000|example|illustrative).*NI",
    "example_household.it_change": r"(£60[,]?000|example|illustrative).*income\s*tax",
    "budget.cost_2026_27": r"(cost|spending).*(2026|£2\.\d)",
    "poverty.absolute_child_bhc.relative_change": r"(absolute\s*child\s*poverty|child poverty.*BHC).*(\-|fall|reduc)",
    "inequality.gini.relative_change": r"gini",
    "result.household_net_income": r"(household\s*net\s*income|net\s*income)",
    "result.income_tax": r"income\s*tax",
    "result.national_insurance": r"national\s*insurance",
    "result.marginal_tax_rate": r"(marginal\s*tax\s*rate|combined\s*marginal)",
}


def extract_b_value(answer: str, field_path: str) -> float | None:
    """Look up a heuristic label for this field path and pull a number."""
    label = FIELD_LABELS.get(field_path)
    if not label:
        return None
    return parse_number_near(answer, label)


def grade_b_scenario(
    responses_for_scenario: list[RunResponse],
) -> dict[str, Any]:
    """Numeric diff + anchor check across the N runs of one B scenario."""
    if not responses_for_scenario:
        return {"error": "no responses"}

    scenario = responses_for_scenario[0].scenario
    reference = scenario.get("reference") or {}
    fixture_rel = reference.get("fixture")
    fixture = None
    fixture_status = "ok"
    if fixture_rel:
        fixture_path = FIXTURES_DIR / Path(fixture_rel).name
        if fixture_path.exists():
            fixture = json.loads(fixture_path.read_text())
        else:
            fixture_status = f"missing: {fixture_path}"

    per_run_results = []
    for r in responses_for_scenario:
        anchor = scenario.get("anchor") or {}
        check = anchor_check(r.answer_text, anchor)

        field_diffs = []
        for fc in reference.get("fields_to_compare") or []:
            path = fc["path"]
            tolerance_pct = fc.get("tolerance_pct", 1.0)
            extracted = extract_b_value(r.answer_text, path)
            expected = fc.get("expected_approx")
            if expected is None and fixture is not None:
                # Pluck the path out of the fixture JSON.
                node = fixture
                for part in path.split("."):
                    if isinstance(node, dict) and part in node:
                        node = node[part]
                    else:
                        node = None
                        break
                if isinstance(node, (int, float)):
                    expected = float(node)
            within = None
            pct_off = None
            if extracted is not None and expected is not None and expected != 0:
                pct_off = abs(extracted - expected) / abs(expected) * 100
                within = pct_off <= tolerance_pct
            field_diffs.append({
                "path": path,
                "expected": expected,
                "extracted": extracted,
                "pct_off": pct_off,
                "tolerance_pct": tolerance_pct,
                "within_tolerance": within,
            })
        per_run_results.append({
            "run_index": r.run_index,
            "anchor": check,
            "field_diffs": field_diffs,
            "tool_call_count": r.meta.get("summary", {}).get("tool_call_count"),
            "http_error": r.meta.get("http_error"),
        })

    # Self-consistency: per-field SD across runs as % of mean.
    sd_by_field: dict[str, float | None] = {}
    field_paths = (
        [d["path"] for d in per_run_results[0]["field_diffs"]]
        if per_run_results else []
    )
    for path in field_paths:
        vals = [
            next(
                (d["extracted"] for d in pr["field_diffs"] if d["path"] == path),
                None,
            )
            for pr in per_run_results
        ]
        clean = [v for v in vals if v is not None]
        if len(clean) >= 2 and mean(clean) != 0:
            sd_by_field[path] = stdev(clean) / abs(mean(clean))
        else:
            sd_by_field[path] = None

    # Aggregate metrics for this scenario.
    all_diff_outcomes = [
        d["within_tolerance"]
        for pr in per_run_results
        for d in pr["field_diffs"]
        if d["within_tolerance"] is not None
    ]
    field_accuracy = (
        sum(1 for x in all_diff_outcomes if x) / len(all_diff_outcomes)
        if all_diff_outcomes else None
    )

    failures = sum(
        1 for pr in per_run_results
        if pr["http_error"] or not any(
            d["within_tolerance"] is not None for d in pr["field_diffs"]
        )
    )
    failure_rate = failures / len(per_run_results)

    return {
        "scenario_id": scenario["id"],
        "fixture_status": fixture_status,
        "per_run_results": per_run_results,
        "self_consistency_sd": sd_by_field,
        "field_accuracy": field_accuracy,
        "failure_rate": failure_rate,
        "max_self_consistency_sd": max(
            (v for v in sd_by_field.values() if v is not None),
            default=None,
        ),
    }


def b_threshold_check(scenario_results: list[dict[str, Any]]) -> dict[str, Any]:
    """Apply SPEC.md Test B thresholds across scenarios."""
    field_accuracies = [
        s["field_accuracy"]
        for s in scenario_results
        if s.get("field_accuracy") is not None
    ]
    overall_field_accuracy = (
        mean(field_accuracies) if field_accuracies else None
    )

    sd_violations = []
    for s in scenario_results:
        for path, sd in (s.get("self_consistency_sd") or {}).items():
            if sd is not None and sd > B_SELF_CONSISTENCY_SD_LIMIT:
                sd_violations.append({
                    "scenario_id": s["scenario_id"],
                    "path": path,
                    "sd_pct_of_mean": sd,
                })

    failure_rates = [s["failure_rate"] for s in scenario_results]
    overall_failure_rate = mean(failure_rates) if failure_rates else None

    # Methodology drift can't be detected purely automatically — flag any
    # scenario where the anchor's must_not_say was violated in any run as a
    # *potential* drift case that the human should review.
    drift_flags = []
    for s in scenario_results:
        for pr in s["per_run_results"]:
            v = pr["anchor"].get("must_not_say_violations") or []
            if v:
                drift_flags.append({
                    "scenario_id": s["scenario_id"],
                    "run_index": pr["run_index"],
                    "violations": v,
                })

    passes = (
        overall_field_accuracy is not None
        and overall_field_accuracy >= B_FIELD_ACCURACY_THRESHOLD
        and not sd_violations
        and len({d["scenario_id"] for d in drift_flags}) <= B_METHODOLOGY_DRIFT_LIMIT
        and overall_failure_rate is not None
        and overall_failure_rate < B_FAILURE_RATE_LIMIT
    )

    return {
        "overall_field_accuracy": overall_field_accuracy,
        "field_accuracy_threshold": B_FIELD_ACCURACY_THRESHOLD,
        "sd_violations": sd_violations,
        "sd_threshold": B_SELF_CONSISTENCY_SD_LIMIT,
        "methodology_drift_flags": drift_flags,
        "methodology_drift_scenarios": len({d["scenario_id"] for d in drift_flags}),
        "methodology_drift_limit": B_METHODOLOGY_DRIFT_LIMIT,
        "overall_failure_rate": overall_failure_rate,
        "failure_rate_limit": B_FAILURE_RATE_LIMIT,
        "passes": bool(passes),
    }


def render_b_results_md(scenario_results: list[dict[str, Any]], threshold: dict[str, Any]) -> str:
    lines = [
        "# Test B — automated grading results",
        "",
        "_Per-scenario numeric diffs, self-consistency, and anchor checks. "
        "Methodology drift is flagged where the anchor's `must_not_say` was "
        "violated — a human should review those for actual drift vs false positives._",
        "",
        f"## Threshold check: {'✅ PASS' if threshold['passes'] else '❌ FAIL'}",
        "",
        f"- Overall field accuracy: **{threshold['overall_field_accuracy']:.0%}**" if threshold['overall_field_accuracy'] is not None else "- Overall field accuracy: n/a",
        f"  (threshold: ≥ {threshold['field_accuracy_threshold']:.0%})",
        f"- Self-consistency violations (SD > {threshold['sd_threshold']:.1%}): **{len(threshold['sd_violations'])}**",
        f"- Methodology drift scenarios: **{threshold['methodology_drift_scenarios']}** "
        f"(threshold: ≤ {threshold['methodology_drift_limit']})",
        f"- Overall failure rate: **{threshold['overall_failure_rate']:.0%}**" if threshold['overall_failure_rate'] is not None else "- Overall failure rate: n/a",
        f"  (threshold: < {threshold['failure_rate_limit']:.0%})",
        "",
    ]
    for s in scenario_results:
        lines.extend([
            f"## {s['scenario_id']}",
            "",
            f"- Fixture: {s['fixture_status']}",
            f"- Field accuracy across runs: {s['field_accuracy']:.0%}" if s.get('field_accuracy') is not None else "- Field accuracy: n/a",
            f"- Max self-consistency SD: {s['max_self_consistency_sd']:.2%}" if s.get('max_self_consistency_sd') is not None else "- Max self-consistency SD: n/a",
            f"- Failure rate: {s['failure_rate']:.0%}",
            "",
        ])
        for pr in s["per_run_results"]:
            tool_n = pr.get("tool_call_count")
            err = pr.get("http_error")
            err_str = f" ⚠ {err}" if err else ""
            lines.append(
                f"### run {pr['run_index']} "
                f"({tool_n} tool calls{err_str})"
            )
            lines.append("")
            anchor = pr["anchor"]
            if anchor.get("checked"):
                mm = anchor.get("must_mention_pass_rate")
                violations = anchor.get("must_not_say_violations") or []
                lines.append(
                    f"- Anchor: must_mention {mm:.0%}, must_not_say "
                    f"violations: {len(violations)}{' ⚠' if violations else ''}"
                )
            if pr["field_diffs"]:
                lines.append("- Field diffs:")
                for d in pr["field_diffs"]:
                    if d["within_tolerance"] is None:
                        if d["expected"] is None and d["extracted"] is None:
                            reason = "no expected (fixture missing?) and no extraction"
                        elif d["expected"] is None:
                            reason = f"extracted={d['extracted']:.2f} but no expected value (fixture missing?)"
                        elif d["extracted"] is None:
                            reason = f"expected={d['expected']:.2f} but couldn't extract from response"
                        else:
                            reason = "expected==0, can't compute % off"
                        lines.append(f"  - ⏭ `{d['path']}`: {reason}")
                    else:
                        mark = "✓" if d["within_tolerance"] else "✗"
                        lines.append(
                            f"  - {mark} `{d['path']}`: "
                            f"extracted={d['extracted']:.2f}, "
                            f"expected={d['expected']:.2f}, "
                            f"off={d['pct_off']:.1f}% (tol {d['tolerance_pct']}%)"
                        )
            lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path, help="A directory under evals/runs/")
    parser.add_argument(
        "--test",
        choices=["A", "B", "both"],
        default="both",
        help="Which test to grade (default: both).",
    )
    parser.add_argument(
        "--threshold-check",
        action="store_true",
        help="After A_grading.md is filled in, parse it and apply Test A "
             "thresholds. Writes threshold_check.md.",
    )
    args = parser.parse_args()

    if not args.run_dir.exists():
        print(f"Run dir not found: {args.run_dir}", file=sys.stderr)
        return 1

    if args.threshold_check:
        sheet_path = args.run_dir / "A_grading.md"
        if not sheet_path.exists():
            print(f"A_grading.md not found in {args.run_dir} — generate it first.", file=sys.stderr)
            return 1
        graded = parse_a_sheet(sheet_path.read_text())
        result = a_threshold_check(graded)
        out = args.run_dir / "threshold_check.md"
        passes = "✅ PASS" if result["passes"] else "❌ FAIL"
        out.write_text(
            f"# Test A — threshold check ({passes})\n\n"
            f"- Mean rubric score: **{result['mean_score']}** "
            f"(threshold ≥ {result['mean_threshold']})\n"
            f"- Fully graded responses: {result['fully_graded_count']}\n"
            f"- Incomplete responses: {result['incomplete_count']}\n"
            f"- Trust-killer failures "
            f"(score < {result['trust_killer_threshold']} on "
            f"{', '.join(A_TRUST_KILLERS)}): "
            f"**{len(result['trust_killer_failures'])}**\n"
            + "".join(
                f"  - {f['scenario_id']} run {f['run_index']}: "
                f"{f['dimension']}={f['score']}\n"
                for f in result["trust_killer_failures"]
            )
            + f"- Fabrication rate: "
            f"{result['fabrication_count']}/{result['fabrication_total']}"
            + (f" ({result['fabrication_rate']:.0%})" if result['fabrication_rate'] is not None else "")
            + f" (limit ≤ {result['fabrication_rate_limit']:.0%})\n"
        )
        (args.run_dir / "threshold_check.json").write_text(json.dumps(result, indent=2))
        print(f"Wrote {out}")
        return 0

    responses = load_run(args.run_dir)
    if not responses:
        print(f"No responses found under {args.run_dir}", file=sys.stderr)
        return 1

    if args.test in ("A", "both"):
        sheet = render_a_sheet(responses)
        out = args.run_dir / "A_grading.md"
        out.write_text(sheet)
        print(f"Wrote {out} (fill in scores and re-run with --threshold-check)")

    if args.test in ("B", "both"):
        b_responses = [r for r in responses if r.scenario["test"] == "B"]
        by_scenario: dict[str, list[RunResponse]] = {}
        for r in b_responses:
            by_scenario.setdefault(r.scenario_id, []).append(r)
        scenario_results = [
            grade_b_scenario(rs) for rs in by_scenario.values()
        ]
        threshold = b_threshold_check(scenario_results)
        (args.run_dir / "B_results.json").write_text(
            json.dumps(
                {"scenarios": scenario_results, "threshold_check": threshold},
                indent=2,
                default=str,
            )
        )
        (args.run_dir / "B_results.md").write_text(
            render_b_results_md(scenario_results, threshold)
        )
        passes = "✅ PASS" if threshold["passes"] else "❌ FAIL"
        print(f"Wrote {args.run_dir / 'B_results.md'} ({passes})")

    return 0


if __name__ == "__main__":
    sys.exit(main())
