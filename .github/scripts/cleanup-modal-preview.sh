#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"
: "${MODAL_SECRET_NAME:?MODAL_SECRET_NAME is required}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${DATABASE_SCHEMA:-}" ]]; then
  export POLICYENGINE_UK_CHAT_MODAL_APP_NAME="$MODAL_APP_NAME"
  export POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME="$MODAL_SECRET_NAME"
  migration_run_name="${MODAL_APP_NAME}-schema-cleanup"
  modal run --name "$migration_run_name" modal_app.py::remove_preview_database_schema
fi
"$script_dir/stop-modal-app.sh"
modal secret delete "$MODAL_SECRET_NAME" --yes --allow-missing
