"""YAML case loading for AI evaluations."""

from pathlib import Path
from typing import Iterable, List

import yaml

from engine.serialization import json_safe
from eval.schemas import (
    AnswerCase,
    DeterministicEvalCase,
    EvalCase,
    LiveAnswerCase,
    LiveGatewayCase,
    LiveToolLoopCase,
    LiveTrajectoryCase,
    ToolContractCase,
    ToolLoopCase,
    TrajectoryCase,
)


DETERMINISTIC_CASE_MODELS = {
    "tool_contract": ToolContractCase,
    "trajectory": TrajectoryCase,
    "answer": AnswerCase,
    "tool_loop": ToolLoopCase,
}

LIVE_CASE_MODELS = {
    "trajectory": LiveTrajectoryCase,
    "answer": LiveAnswerCase,
    "tool_loop": LiveToolLoopCase,
    "gateway": LiveGatewayCase,
}


def _validate_deterministic_case(case: DeterministicEvalCase, path: Path) -> None:
    if "live_model" in case.requirements:
        raise ValueError(
            f"{path}: deterministic cases cannot require a live model"
        )
    if isinstance(case, TrajectoryCase) and case.offline_response is None:
        raise ValueError(
            f"{path}: deterministic trajectory cases require offline_response"
        )
    if isinstance(case, AnswerCase) and case.offline_response is None:
        raise ValueError(
            f"{path}: deterministic answer cases require offline_response"
        )
    if isinstance(case, ToolLoopCase) and not case.offline_responses:
        raise ValueError(
            f"{path}: deterministic tool-loop cases require offline_responses"
        )


def load_case_file(path: Path, *, live: bool = False) -> List[EvalCase]:
    data = yaml.safe_load(path.read_text()) or {}
    raw_cases = data.get("cases", data if isinstance(data, list) else [data])
    cases: List[EvalCase] = []
    case_models = LIVE_CASE_MODELS if live else DETERMINISTIC_CASE_MODELS
    for raw in raw_cases:
        raw = json_safe(raw)
        if not isinstance(raw, dict):
            raise ValueError(f"{path}: every eval case must be an object")
        suite = raw.get("suite")
        if suite not in case_models:
            layer = "live" if live else "deterministic"
            raise ValueError(f"{path}: unknown {layer} suite {suite!r}")
        case = case_models[suite].model_validate(raw)
        if live:
            cases.append(case)
            continue
        _validate_deterministic_case(case, path)
        cases.append(case)
    return cases


def load_cases(paths: Iterable[Path], *, live: bool = False) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for path in sorted(paths):
        cases.extend(load_case_file(path, live=live))
    return cases
