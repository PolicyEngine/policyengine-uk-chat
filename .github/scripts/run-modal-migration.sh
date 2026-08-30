#!/usr/bin/env bash

set -euo pipefail

if [[ -n "${MODAL_APP_NAME:-}" ]]; then
  : "${MODAL_SECRET_NAME:?MODAL_SECRET_NAME is required with MODAL_APP_NAME}"
  export POLICYENGINE_UK_CHAT_MODAL_APP_NAME="$MODAL_APP_NAME"
  export POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME="$MODAL_SECRET_NAME"
fi

migration_run_name="${MODAL_APP_NAME:-policyengine-uk-chat}-migration"
modal run --name "$migration_run_name" modal_app.py::migrate
