#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"
: "${MODAL_SECRET_NAME:?MODAL_SECRET_NAME is required}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ -n "${DATABASE_SCHEMA:-}" ]]; then
  secret_status="$(
    modal secret list --json |
      python "$script_dir/modal-secret-status.py" "$MODAL_SECRET_NAME"
  )"
  if [[ "$secret_status" == "present" ]]; then
    export POLICYENGINE_UK_CHAT_MODAL_APP_NAME="$MODAL_APP_NAME"
    export POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME="$MODAL_SECRET_NAME"
    migration_run_name="${MODAL_APP_NAME}-schema-cleanup"
    modal run --name "$migration_run_name" modal_app.py::remove_preview_database_schema
  else
    # The schema removal runs with the preview secret, and a preview schema
    # can't exist without one: deploy seeds the secret before its migration
    # creates the schema, and cleanup deletes the secret only after dropping
    # the schema. So the PR never deployed a preview (as while preview
    # deploys are paused) or was already cleaned up.
    echo "Modal secret '$MODAL_SECRET_NAME' is missing; no preview schema to remove."
  fi
fi
"$script_dir/stop-modal-app.sh"
modal secret delete "$MODAL_SECRET_NAME" --yes --allow-missing
