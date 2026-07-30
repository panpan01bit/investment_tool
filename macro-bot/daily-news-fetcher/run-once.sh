#!/bin/bash
# Run once for manual testing
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR" || exit 1
python3 fetch.py "$@"
python3.8 akshare_fetcher.py "$@"
