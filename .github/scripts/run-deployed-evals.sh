#!/usr/bin/env bash

set -euo pipefail

: "${EVAL_BACKEND_URL:?EVAL_BACKEND_URL is required}"
: "${EVAL_RUN_TOKEN:?EVAL_RUN_TOKEN is required}"

args=(
  --case-file evals/cases/tool_loop/uk_population_live.yaml
  --trial-timeout-seconds "${EVAL_TRIAL_TIMEOUT_SECONDS:-600}"
  --concurrency "${EVAL_CONCURRENCY:-4}"
  --report-dir evals/reports
)

if [[ -n "${EVAL_CASE_ID:-}" ]]; then
  args+=(--case-id "$EVAL_CASE_ID")
fi

PYTHONPATH=backend python -m eval.run_deployed "${args[@]}"
