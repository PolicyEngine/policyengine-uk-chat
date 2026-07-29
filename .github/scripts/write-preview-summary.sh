#!/usr/bin/env bash

set -euo pipefail

: "${FRONTEND_URL:?FRONTEND_URL is required}"
: "${BACKEND_URL:?BACKEND_URL is required}"
: "${GITHUB_STEP_SUMMARY:?GITHUB_STEP_SUMMARY is required}"

{
  echo "## Beta preview"
  echo
  echo "- Frontend: $FRONTEND_URL"
  echo "- Backend: $BACKEND_URL"
} >> "$GITHUB_STEP_SUMMARY"
