#!/usr/bin/env bash
set -euo pipefail
URL="${1:-http://127.0.0.1:8000}"
echo "Running smoke check against $URL..."
status=$(curl -s -o /dev/null -w "%{http_code}" "$URL/health")
if [ "$status" -eq 200 ]; then
  echo "Smoke check passed: HTTP 200 from $URL/health"
  exit 0
else
  echo "Smoke check failed: HTTP $status from $URL/health"
  exit 1
fi
