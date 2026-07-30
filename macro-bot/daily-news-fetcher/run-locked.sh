#!/bin/bash
# run-locked.sh — wrapper for cron to prevent overlapping daily-news-fetcher runs.
# Usage: run-locked.sh [extra args passed to fetch.py and akshare_fetcher.py]
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LOCK_FILE="/tmp/macro-bot-fetch.lock"
LOG_FILE="${SCRIPT_DIR}/logs/cron.log"
mkdir -p "$(dirname "$LOG_FILE")"

# Preserve "$@" across the nested flock -c shell by re-quoting as a single string.
# flock -c runs the rest as a shell command, so we hand it already-joined argv.
ARGS_STR=""
for a in "$@"; do
    ARGS_STR+=" $(printf '%q' "$a")"
done

exec /usr/bin/flock -n "$LOCK_FILE" -c "
  cd '$SCRIPT_DIR'
  echo \"\$(date +%Y-%m-%dT%H:%M:%S) fetch.py start\"
  /usr/bin/python3 fetch.py$ARGS_STR >> '$LOG_FILE' 2>&1
  echo \"\$(date +%Y-%m-%dT%H:%M:%S) akshare_fetcher.py start\"
  /usr/bin/python3.8 akshare_fetcher.py$ARGS_STR >> '$LOG_FILE' 2>&1
  echo \"\$(date +%Y-%m-%dT%H:%M:%S) done\"
"
