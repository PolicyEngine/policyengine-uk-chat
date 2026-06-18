"""YAML case loading for manual AI evaluations."""

from pathlib import Path
from typing import Iterable, List

import yaml

from eval.schemas import (
    AnswerCase,
    EvalCase,
    GatewayCase,
    ToolContractCase,
    ToolLoopCase,
    TrajectoryCase,
)


CASE_MODELS = {
    "tool_contract": ToolContractCase,
    "trajectory": TrajectoryCase,
    "answer": AnswerCase,
    "tool_loop": ToolLoopCase,
    "gateway": GatewayCase,
}


def load_case_file(path: Path) -> List[EvalCase]:
    data = yaml.safe_load(path.read_text()) or {}
    raw_cases = data.get("cases", data if isinstance(data, list) else [data])
    cases: List[EvalCase] = []
    for raw in raw_cases:
        suite = raw.get("suite")
        if suite not in CASE_MODELS:
            raise ValueError(f"{path}: unknown suite {suite!r}")
        cases.append(CASE_MODELS[suite].model_validate(raw))
    return cases


def load_cases(paths: Iterable[Path]) -> List[EvalCase]:
    cases: List[EvalCase] = []
    for path in sorted(paths):
        cases.extend(load_case_file(path))
    return cases
