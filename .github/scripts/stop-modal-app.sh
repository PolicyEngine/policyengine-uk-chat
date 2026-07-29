#!/usr/bin/env bash

set -euo pipefail

: "${MODAL_APP_NAME:?MODAL_APP_NAME is required}"

modal app stop "$MODAL_APP_NAME" || true
