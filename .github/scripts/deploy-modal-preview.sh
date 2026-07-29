#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"
: "${MODAL_SECRET_NAME:?MODAL_SECRET_NAME is required}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT is required}"

POLICYENGINE_UK_CHAT_MODAL_APP_NAME="$MODAL_APP_NAME" \
POLICYENGINE_UK_CHAT_MODAL_SECRET_NAME="$MODAL_SECRET_NAME" \
  modal deploy modal_app.py --name "$MODAL_APP_NAME"

modal_url="$(
  python - <<'PY'
import os

import modal

function = modal.Function.from_name(os.environ["MODAL_APP_NAME"], "web")
print(function.get_web_url())
PY
)"

if [[ -z "$modal_url" || "$modal_url" == "None" ]]; then
  echo "Modal did not return a web URL for $MODAL_APP_NAME." >&2
  exit 1
fi

echo "modal_url=$modal_url" >> "$GITHUB_OUTPUT"
