"""Detached, parallel Modal runner for deployed UK population evaluations."""

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import modal


APP_NAME = os.environ.get(
    "POLICYENGINE_UK_CHAT_EVAL_MODAL_APP_NAME",
    "policyengine-uk-chat-evals",
)
SECRET_NAME = os.environ.get(
    "POLICYENGINE_UK_CHAT_EVAL_MODAL_SECRET_NAME",
    "policyengine-uk-chat-secrets",
)
REPORT_VOLUME_NAME = os.environ.get(
    "POLICYENGINE_UK_CHAT_EVAL_MODAL_VOLUME_NAME",
    "policyengine-uk-chat-eval-reports",
)

LOCAL_ROOT = Path(__file__).resolve().parent
LOCAL_CASE_FILE = LOCAL_ROOT / "evals/cases/tool_loop/uk_population_live.yaml"
REMOTE_CASE_FILE = Path("/app/evals/cases/tool_loop/uk_population_live.yaml")
REPORT_MOUNT = "/reports"

app = modal.App(APP_NAME)
image = (
    modal.Image.debian_slim(python_version="3.13")
    .pip_install_from_requirements("backend/requirements-modal-eval.txt")
    .add_local_dir("backend", remote_path="/app/backend", copy=True)
    .add_local_dir("evals", remote_path="/app/evals", copy=True)
)
eval_secret = modal.Secret.from_name(SECRET_NAME)
report_volume = modal.Volume.from_name(
    REPORT_VOLUME_NAME,
    create_if_missing=True,
    version=2,
)


def _local_git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=LOCAL_ROOT,
        text=True,
    ).strip()


def _local_case_ids() -> list[str]:
    sys.path.insert(0, str(LOCAL_ROOT / "backend"))
    from eval.modal_batch import load_deployed_case_ids

    case_ids = load_deployed_case_ids(LOCAL_CASE_FILE)
    if len(case_ids) != 20:
        raise ValueError(f"expected 20 UK population eval cases, found {len(case_ids)}")
    return case_ids


@app.function(
    image=image,
    secrets=[eval_secret],
    volumes={REPORT_MOUNT: report_volume},
    cpu=0.25,
    memory=512,
    timeout=1_800,
    max_containers=25,
    scaledown_window=60,
    region="eu",
)
def evaluate_case(
    case_id: str,
    *,
    backend_url: str,
    run_id: str,
    git_sha: str,
    trial_timeout_seconds: int,
) -> dict:
    sys.path.insert(0, "/app/backend")

    from eval.deployed_runner import run_deployed_eval
    from eval.modal_batch import case_report_filename, safe_identifier

    token = os.environ.get("UK_CHAT_EVAL_TOKEN")
    if not token:
        raise RuntimeError("UK_CHAT_EVAL_TOKEN is not configured")

    safe_run_id = safe_identifier(run_id)
    report = asyncio.run(
        run_deployed_eval(
            case_file=REMOTE_CASE_FILE,
            backend_url=backend_url,
            token=token,
            timeout_seconds=trial_timeout_seconds,
            concurrency=3,
            case_id=case_id,
            write_reports=False,
        )
    ).model_copy(update={"git_sha": git_sha})

    report_dir = Path(REPORT_MOUNT) / safe_run_id
    report_dir.mkdir(parents=True, exist_ok=True)
    report_path = report_dir / case_report_filename(case_id)
    report_path.write_text(report.model_dump_json(indent=2))
    report_volume.commit()

    result = report.results[0]
    summary = {
        "run_id": safe_run_id,
        "case_id": case_id,
        "status": result.status,
        "score": result.score,
        "report_path": str(report_path),
    }
    print(json.dumps({"event": "eval_case_completed", **summary}, sort_keys=True))
    return summary


@app.local_entrypoint()
def main(
    backend_url: str,
    run_id: str = "",
    git_sha: str = "",
    trial_timeout_seconds: int = 600,
):
    if not backend_url.startswith(("https://", "http://")):
        raise ValueError("backend_url must be an HTTP(S) URL")
    if trial_timeout_seconds < 1:
        raise ValueError("trial_timeout_seconds must be positive")

    case_ids = _local_case_ids()
    resolved_run_id = run_id or (
        datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    resolved_git_sha = git_sha or _local_git_sha()

    submission = {
        "event": "eval_batch_submitted",
        "app": APP_NAME,
        "backend_url": backend_url,
        "case_count": len(case_ids),
        "git_sha": resolved_git_sha,
        "max_containers": 25,
        "report_volume": REPORT_VOLUME_NAME,
        "run_id": resolved_run_id,
    }
    print(json.dumps(submission, sort_keys=True))
    evaluate_case.spawn_map(
        case_ids,
        kwargs={
            "backend_url": backend_url,
            "run_id": resolved_run_id,
            "git_sha": resolved_git_sha,
            "trial_timeout_seconds": trial_timeout_seconds,
        },
    )
