#!/usr/bin/env bash
# scripts/refresh-token.sh
# Refresh Kimi CLI access token before it expires (valid ~15 minutes).
# Intended to run from a systemd timer or cron on the server, or from a
# LaunchAgent on macOS.
#
# Behavior:
#   - Reads ~/.kimi-code/credentials/kimi-code.json
#   - If expires_at is > 10 minutes away, exit silently
#   - Otherwise run `kimi login` to refresh the access_token
#   - Logs to ~/.kimi-code/logs/refresh-token.log
#
# Requires:
#   - Kimi CLI installed and on PATH
#   - jq or python3 to parse JSON

set -euo pipefail

CRED_DIR="${HOME}/.kimi-code/credentials"
CRED_FILE="${CRED_DIR}/kimi-code.json"
LOG_DIR="${HOME}/.kimi-code/logs"
LOG_FILE="${LOG_DIR}/refresh-token.log"
KIMI_BIN="${HOME}/.kimi-code/bin/kimi"

mkdir -p "${LOG_DIR}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"${LOG_FILE}"
}

if [[ ! -f "${CRED_FILE}" ]]; then
    log "[ERROR] credentials file not found: ${CRED_FILE}. Run 'kimi login' first."
    exit 1
fi

# Read expires_at
EXPIRES_AT=0
if command -v jq >/dev/null 2>&1; then
    EXPIRES_AT=$(jq -r '.expires_at // 0' "${CRED_FILE}")
else
    EXPIRES_AT=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("expires_at",0))' "${CRED_FILE}")
fi

NOW=$(date +%s)
REMAINING=$((EXPIRES_AT - NOW))

# Refresh if fewer than 10 minutes remaining
if [[ ${REMAINING} -gt 600 ]]; then
    log "[SKIP] token still valid for ${REMAINING}s"
    exit 0
fi

log "[REFRESH] token expires in ${REMAINING}s, refreshing..."

if "${KIMI_BIN}" login 2>>"${LOG_FILE}"; then
    log "[OK] token refreshed"
else
    log "[ERROR] refresh failed, exit_code=$?"
    exit 1
fi
