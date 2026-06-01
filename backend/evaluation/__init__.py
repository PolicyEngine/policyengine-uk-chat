"""Manual AI evaluation harness for the UK chat runtime."""

from evaluation.runner import run_eval
from evaluation.schemas import EvalReport

__all__ = ["EvalReport", "run_eval"]
