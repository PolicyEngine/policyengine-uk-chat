#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
app_status="$(
  modal app list --json |
    python3 "$script_dir/modal-app-status.py" "$MODAL_APP_NAME"
)"

if [[ "$app_status" == "missing" || "$app_status" == "stopped" ]]; then
  echo "Modal app '$MODAL_APP_NAME' is already $app_status."
  exit 0
fi

modal app stop "$MODAL_APP_NAME" --yes
