#!/usr/bin/env bash

set -euo pipefail

: "${BACKEND_URL:?BACKEND_URL is required}"

attempts="${SMOKE_TEST_ATTEMPTS:-12}"
curl_timeout="${SMOKE_TEST_CURL_TIMEOUT_SECONDS:-15}"
retry_delay="${SMOKE_TEST_RETRY_DELAY_SECONDS:-10}"
frontend_url="${FRONTEND_URL:-}"
backend_url="${BACKEND_URL%/}"

for attempt in $(seq 1 "$attempts"); do
  health_code="$(
    curl -s -o /dev/null -w "%{http_code}" \
      --max-time "$curl_timeout" \
      "$backend_url/version" || true
  )"

  if [[ -z "$frontend_url" ]]; then
    if [[ "$health_code" == "200" ]]; then
      echo "Backend healthy after deploy (attempt $attempt)."
      exit 0
    fi
    echo "Attempt $attempt: got '$health_code'; retrying in ${retry_delay}s..."
  else
    cors_code="$(
      curl -s -o /dev/null -w "%{http_code}" \
        --max-time "$curl_timeout" \
        -X OPTIONS "$backend_url/chat/message" \
        -H "Origin: $frontend_url" \
        -H "Access-Control-Request-Method: POST" \
        -H "Access-Control-Request-Headers: content-type" || true
    )"
    if [[ "$health_code" == "200" && "$cors_code" == "200" ]]; then
      echo "Preview backend healthy with working CORS after deploy (attempt $attempt)."
      exit 0
    fi
    echo "Attempt $attempt: health '$health_code', CORS '$cors_code'; retrying in ${retry_delay}s..."
  fi

  sleep "$retry_delay"
done

if [[ -z "$frontend_url" ]]; then
  echo "Deployed backend failed the smoke test: $backend_url/version" >&2
else
  echo "Preview backend failed health or CORS smoke tests: $backend_url" >&2
fi
exit 1
