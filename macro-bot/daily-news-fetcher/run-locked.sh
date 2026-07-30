#!/bin/bash
# run-locked.sh — wrapper for cron to prevent overlapping daily-news-fetcher runs.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="/tmp/macro-bot-fetch.lock"
LOG_FILE="${SCRIPT_DIR}/logs/cron.log"
mkdir -p "$(dirname "$LOG_FILE")"

exec /usr/bin/flock -n "$LOCK_FILE" -c "
  cd '$SCRIPT_DIR'
  echo '\$(date +%Y-%m-%dT%H:%M:%S) fetch.py start'
  /usr/bin/python3 fetch.py \\"$@\\" >> '$LOG_FILE' 2>&1
  echo '\$(date +%Y-%m-%dT%H:%M:%S) akshare_fetcher.py start'
  /usr/bin/python3.8 akshare_fetcher.py \\"$@\\" >> '$LOG_FILE' 2>&1
  echo '\$(date +%Y-%m-%dT%H:%M:%S) done'
"
