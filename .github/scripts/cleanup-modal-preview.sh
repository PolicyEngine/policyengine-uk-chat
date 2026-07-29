#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"
: "${MODAL_SECRET_NAME:?MODAL_SECRET_NAME is required}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
"$script_dir/stop-modal-app.sh"
modal secret delete "$MODAL_SECRET_NAME" --yes --allow-missing
