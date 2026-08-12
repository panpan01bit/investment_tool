#!/usr/bin/env bash
# scripts/refresh-token.sh
# Refresh Kimi Code access token before it expires (valid ~15 minutes).
# Intended to run from a systemd timer or cron on the server.
#
# Behavior:
#   - Reads ~/.kimi-code/credentials/kimi-code.json
#   - If expires_at is > 10 minutes away, exit silently
#   - Otherwise trigger the kimi-datasource MCP plugin, which refreshes the
#     access_token on demand.
#   - Logs to ~/.kimi-code/logs/refresh-token.log
#
# Requires:
#   - Node.js and the kimi-datasource plugin installed under ~/.kimi-code
#   - Python venv with mcp package
#   - .env containing KIMI_DATASOURCE_* variables

set -euo pipefail

CRED_DIR="${HOME}/.kimi-code/credentials"
CRED_FILE="${CRED_DIR}/kimi-code.json"
LOG_DIR="${HOME}/.kimi-code/logs"
LOG_FILE="${LOG_DIR}/refresh-token.log"
MACRO_BOT_DIR="/www/wwwroot/investment_tool/macro-bot"
VENV_PYTHON="/www/wwwroot/.venv311/bin/python"

mkdir -p "${LOG_DIR}"

log() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') $*" >>"${LOG_FILE}"
}

if [[ ! -f "${CRED_FILE}" ]]; then
    log "[ERROR] credentials file not found: ${CRED_FILE}. Copy credentials from a logged-in machine first."
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

log "[REFRESH] token expires in ${REMAINING}s, refreshing via kimi-datasource plugin..."

cd "${MACRO_BOT_DIR}"
set -a
source .env
set +a

if "${VENV_PYTHON}" -c "from kimi_datasource_client import run_get_desc; run_get_desc('arxiv')" >>"${LOG_FILE}" 2>&1; then
    NEW_EXPIRES=$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("expires_at",0))' "${CRED_FILE}")
    log "[OK] token refreshed, expires_at=${NEW_EXPIRES}"
else
    log "[ERROR] refresh failed"
    exit 1
fi

