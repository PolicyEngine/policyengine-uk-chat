"""Contract tests for pull-request verification jobs."""

from pathlib import Path

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TESTS_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "tests.yml"


def _jobs() -> dict:
    with TESTS_WORKFLOW.open(encoding="utf-8") as workflow_file:
        workflow = yaml.safe_load(workflow_file)
    return workflow["jobs"]


def test_every_eval_suite_runs_on_every_pull_request():
    jobs = _jobs()

    assert "changes" not in jobs

    deterministic = jobs["deterministic-evals"]
    assert "if" not in deterministic
    assert "needs" not in deterministic
    deterministic_cases = deterministic["strategy"]["matrix"]["include"]
    assert {case["target"] for case in deterministic_cases} == {
        "eval-tool-contracts",
        "eval-scripted-trajectories",
        "eval-scripted-answers",
        "eval-scripted-tool-loops",
    }
    tool_contracts = next(
        case
        for case in deterministic_cases
        if case["target"] == "eval-tool-contracts"
    )
    assert tool_contracts["requires_data"] is True
    assert deterministic["env"]["RUN_DATA_EVALS"] == (
        "${{ matrix.requires_data && '1' || '0' }}"
    )

    live = jobs["live-model-evals"]
    assert "if" not in live
    assert "needs" not in live
    live_cases = live["strategy"]["matrix"]["include"]
    assert {case["target"] for case in live_cases} == {
        "eval-ai-live-gateway",
        "eval-ai-live-trajectory",
        "eval-ai-live-answer",
        "eval-ai-live-tool-loop",
    }

    enhanced_frs = jobs["enhanced-frs"]
    assert "if" not in enhanced_frs
    assert "needs" not in enhanced_frs
