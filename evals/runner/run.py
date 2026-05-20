#!/usr/bin/env python3
"""
Eval runner — load scenario YAMLs from evals/scenarios/, POST each one N
times to the configured chat backend, save raw SSE + extracted text +
summary JSON per run under evals/runs/<timestamp>/.

No grading. The runner only produces the conversation logs. Grading is a
separate step.

Usage:
    python evals/runner/run.py                  # all scenarios
    python evals/runner/run.py a1_mechanism b1_society_wide_pa
    python evals/runner/run.py --dry-run        # show what would run
    python evals/runner/run.py --backend-url X  # override the chat URL

Environment variables:
    UK_CHAT_BACKEND_URL       Chat backend base URL (default: the PR 51 preview)
    UK_CHAT_BYPASS_TOKEN      Optional Vercel "Protection Bypass for Automation"
                              token, appended as a query param so the runner can
                              reach a protected preview without SSO.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import yaml


# Paths
EVALS_DIR = Path(__file__).resolve().parent.parent
SCENARIOS_DIR = EVALS_DIR / "scenarios"
RUNS_DIR = EVALS_DIR / "runs"

# Defaults
DEFAULT_BACKEND_URL = os.environ.get(
    "UK_CHAT_BACKEND_URL",
    # PR 51 preview backend — the only deploy with model_backend + scenario_context wired.
    "https://policyengine--peukchat-feat-model-backend-selector-web.modal.run",
)
BYPASS_TOKEN = os.environ.get("UK_CHAT_BYPASS_TOKEN") or None
REQUEST_TIMEOUT_SECONDS = 900  # economy-wide sims can take a few minutes


# ---------------------------------------------------------------------------
# Scenario loading
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    id: str
    test: str  # "A" or "B"
    title: str
    prompt: str
    model_backend: str
    num_runs: int
    scenario_context: str | None
    raw: dict[str, Any]  # full YAML dict, kept for logging

    @classmethod
    def from_yaml(cls, path: Path) -> "Scenario":
        data = yaml.safe_load(path.read_text())
        return cls(
            id=data["id"],
            test=data["test"],
            title=data["title"],
            prompt=data["prompt"],
            model_backend=data["chat_settings"]["model_backend"],
            num_runs=data["chat_settings"].get("num_runs", 3),
            scenario_context=data.get("scenario_context"),
            raw=data,
        )


def load_scenarios(filter_ids: list[str] | None = None) -> list[Scenario]:
    scenarios = []
    for path in sorted(SCENARIOS_DIR.glob("*.yaml")):
        scenario = Scenario.from_yaml(path)
        if filter_ids and scenario.id not in filter_ids:
            continue
        scenarios.append(scenario)
    if filter_ids:
        loaded_ids = {s.id for s in scenarios}
        missing = set(filter_ids) - loaded_ids
        if missing:
            raise SystemExit(f"Unknown scenario IDs: {sorted(missing)}")
    return scenarios


# ---------------------------------------------------------------------------
# Chat backend interaction
# ---------------------------------------------------------------------------

def build_request_payload(scenario: Scenario) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model_backend": scenario.model_backend,
        "messages": [{"role": "user", "content": scenario.prompt}],
    }
    if scenario.scenario_context:
        payload["scenario_context"] = scenario.scenario_context
    return payload


def build_request_url(backend_url: str) -> str:
    url = f"{backend_url.rstrip('/')}/chat/message"
    if BYPASS_TOKEN:
        sep = "&" if "?" in url else "?"
        url = (
            f"{url}{sep}x-vercel-protection-bypass={BYPASS_TOKEN}"
            f"&x-vercel-set-bypass-cookie=samesitenone"
        )
    return url


def parse_sse(sse_text: str) -> list[dict[str, Any]]:
    """Parse SSE lines into a list of event dicts."""
    events = []
    for line in sse_text.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            events.append(json.loads(line[len("data: "):]))
        except json.JSONDecodeError:
            # The model occasionally emits non-JSON lines (e.g. heartbeats);
            # drop them rather than crashing the whole run.
            pass
    return events


def summarise_events(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Pull useful aggregates out of the SSE stream."""
    counts: dict[str, int] = {}
    for e in events:
        t = e.get("type", "?")
        counts[t] = counts.get(t, 0) + 1

    full_text = "".join(
        e.get("content", "") for e in events if e.get("type") == "chunk"
    )

    # The `done` event carries usage / billing — last one wins.
    done = next(
        (e for e in reversed(events) if e.get("type") == "done"),
        None,
    )

    errors = [e for e in events if e.get("type") == "error"]

    return {
        "event_counts": counts,
        "answer_text": full_text,
        "answer_length_chars": len(full_text),
        "tool_call_count": counts.get("tool_use", 0),
        "completed": done is not None,
        "error_count": len(errors),
        "errors": errors,
        "done_event": done,  # contains usage, session_id, model, model_backend
    }


def run_single(
    *,
    scenario: Scenario,
    backend_url: str,
    run_index: int,
    out_dir: Path,
) -> dict[str, Any]:
    """POST one scenario once, save SSE + extracted text + summary."""
    url = build_request_url(backend_url)
    payload = build_request_payload(scenario)

    started = dt.datetime.now(dt.timezone.utc)

    sse_text = ""
    http_error: str | None = None
    try:
        with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
            with client.stream("POST", url, json=payload) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_text():
                    sse_text += chunk
    except httpx.HTTPStatusError as e:
        http_error = (
            f"HTTP {e.response.status_code}: "
            f"{(e.response.text or '')[:500]}"
        )
    except httpx.RequestError as e:
        http_error = f"Request error: {type(e).__name__}: {e}"

    finished = dt.datetime.now(dt.timezone.utc)
    elapsed_seconds = (finished - started).total_seconds()

    # Persist artifacts
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"run-{run_index}.sse").write_text(sse_text)

    events = parse_sse(sse_text)
    summary = summarise_events(events)
    (out_dir / f"run-{run_index}.txt").write_text(summary["answer_text"])

    meta = {
        "scenario_id": scenario.id,
        "test": scenario.test,
        "run_index": run_index,
        "url": re.sub(r"protection-bypass=[^&]+", "protection-bypass=REDACTED", url),
        "model_backend": scenario.model_backend,
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "elapsed_seconds": round(elapsed_seconds, 1),
        "http_error": http_error,
        "summary": {
            k: v
            for k, v in summary.items()
            # Don't duplicate the full answer text in JSON — it's already in run-N.txt.
            if k != "answer_text"
        },
    }
    (out_dir / f"run-{run_index}.meta.json").write_text(json.dumps(meta, indent=2))

    return meta


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def make_run_dir() -> Path:
    timestamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    return RUNS_DIR / timestamp


def run_all(
    *,
    scenarios: list[Scenario],
    backend_url: str,
    dry_run: bool,
) -> Path | None:
    if dry_run:
        print(f"DRY RUN (backend: {backend_url})\n")
        for s in scenarios:
            print(
                f"  {s.id:30}  test={s.test}  runs={s.num_runs}  "
                f"backend={s.model_backend}  ctx={'yes' if s.scenario_context else 'no'}"
            )
        total = sum(s.num_runs for s in scenarios)
        print(f"\nWould execute {total} requests across {len(scenarios)} scenarios.")
        return None

    run_dir = make_run_dir()
    run_dir.mkdir(parents=True, exist_ok=True)

    manifest = {
        "started_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "backend_url": backend_url,
        "bypass_token_set": BYPASS_TOKEN is not None,
        "scenarios": [s.id for s in scenarios],
        "runs": [],
    }
    manifest_path = run_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2))

    print(f"Run output: {run_dir}\n")

    for scenario in scenarios:
        scenario_dir = run_dir / scenario.id
        # Save a copy of the scenario YAML alongside the runs so we can
        # tell, months later, exactly what prompt/anchor was in effect.
        scenario_dir.mkdir(parents=True, exist_ok=True)
        (scenario_dir / "scenario.yaml").write_text(yaml.safe_dump(scenario.raw))

        print(f"=== {scenario.id} ({scenario.test}, {scenario.num_runs} runs) ===")
        for i in range(1, scenario.num_runs + 1):
            print(f"  run {i}/{scenario.num_runs}...", end=" ", flush=True)
            meta = run_single(
                scenario=scenario,
                backend_url=backend_url,
                run_index=i,
                out_dir=scenario_dir,
            )
            elapsed = meta["elapsed_seconds"]
            err = meta["http_error"]
            tools = meta["summary"]["tool_call_count"]
            chars = meta["summary"]["answer_length_chars"]
            status = (
                f"ERR ({err})" if err
                else f"ok  {chars} chars, {tools} tool calls, {elapsed}s"
            )
            print(status)

            manifest["runs"].append({
                "scenario_id": scenario.id,
                "run_index": i,
                "elapsed_seconds": elapsed,
                "http_error": err,
                "tool_call_count": tools,
                "answer_length_chars": chars,
            })
            manifest_path.write_text(json.dumps(manifest, indent=2))
        print()

    manifest["finished_at"] = dt.datetime.now(dt.timezone.utc).isoformat()
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Done. Logs in {run_dir}")
    return run_dir


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "scenario_ids",
        nargs="*",
        help="Optional list of scenario IDs to run. Empty = run all.",
    )
    parser.add_argument(
        "--backend-url",
        default=DEFAULT_BACKEND_URL,
        help="Chat backend base URL. Defaults to UK_CHAT_BACKEND_URL env var or PR 51 preview.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would run without making any requests.",
    )
    args = parser.parse_args()

    scenarios = load_scenarios(args.scenario_ids or None)
    if not scenarios:
        print("No scenarios matched.", file=sys.stderr)
        return 1

    run_all(
        scenarios=scenarios,
        backend_url=args.backend_url,
        dry_run=args.dry_run,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
