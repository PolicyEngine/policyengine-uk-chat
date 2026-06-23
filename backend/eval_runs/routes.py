"""HTTP routes for reading stored eval reports."""

import os

from fastapi import APIRouter, Depends, Header, HTTPException

from eval_runs.schemas import EvalRunComparison, EvalRunDetail, EvalRunSummary
from eval_runs.store import compare_runs, get_detail, list_summaries

router = APIRouter(prefix="/eval-runs", tags=["eval-runs"])


def require_eval_dashboard_token(authorization: str | None = Header(default=None)) -> None:
    token = os.environ.get("EVAL_DASHBOARD_TOKEN")
    if not token:
        raise HTTPException(status_code=404, detail="Eval dashboard is not enabled")
    if authorization != f"Bearer {token}":
        raise HTTPException(status_code=401, detail="Unauthorized")


@router.get("", response_model=list[EvalRunSummary])
def list_eval_runs(
    limit: int = 50,
    offset: int = 0,
    mode: str | None = None,
    provider: str | None = None,
    suite: str | None = None,
    status: str | None = None,
    q: str | None = None,
    _: None = Depends(require_eval_dashboard_token),
):
    limit = max(1, min(limit, 200))
    offset = max(0, offset)
    return list_summaries(
        limit=limit,
        offset=offset,
        mode=mode,
        provider=provider,
        suite=suite,
        status=status,
        q=q,
    )


@router.get("/compare", response_model=EvalRunComparison)
def compare_eval_runs(
    base_id: str,
    head_id: str,
    _: None = Depends(require_eval_dashboard_token),
):
    return compare_runs(base_id, head_id)


@router.get("/{run_id}", response_model=EvalRunDetail)
def get_eval_run(
    run_id: str,
    _: None = Depends(require_eval_dashboard_token),
):
    return get_detail(run_id)
