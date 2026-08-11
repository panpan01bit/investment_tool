#!/usr/bin/env bash
# scripts/setup-kimi-datasource.sh
# One-time server setup for Kimi CLI and the kimi-datasource plugin.
# Run as root on the server (the same user that runs static-server.service).
#
# What it does:
#   1. Installs Node.js 22.x if not present
#   2. Installs Kimi CLI (https://kimi.com/code)
#   3. Installs the official kimi-datasource plugin
#   4. Performs an interactive browser login (one time)
#   5. Installs a systemd timer to refresh the access token every 10 minutes
#
# After running this script:
#   - /root/.kimi-code/credentials/kimi-code.json will hold OAuth tokens.
#   - The systemd timer will keep refreshing the access_token automatically.
#   - macro-bot can spawn the plugin via kimi_datasource_client.py.

set -euo pipefail

KIMI_HOME="${HOME}/.kimi-code"
KIMI_BIN="${KIMI_HOME}/bin/kimi"
PLUGIN_DIR="${KIMI_HOME}/plugins/managed/kimi-datasource"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $*"
}

command_exists() {
    command -v "$1" >/dev/null 2>&1
}

# 1. Node.js
if ! command_exists node; then
    log "Installing Node.js 22.x..."
    curl -fsSL https://deb.nodesource.com/setup_22.x | bash -
    apt-get install -y nodejs
else
    log "Node.js already installed: $(node --version)"
fi

# 2. Kimi CLI
if ! command_exists kimi; then
    log "Installing Kimi CLI..."
    curl -fsSL https://kimi.com/kimi-code/install.sh | sh
    log "Kimi CLI installed: $(kimi --version)"
else
    log "Kimi CLI already installed: $(kimi --version)"
fi

# Ensure PATH includes ~/.kimi-code/bin for the rest of the script
export PATH="${KIMI_HOME}/bin:${PATH}"

# 3. Install kimi-datasource plugin
if [[ -d "${PLUGIN_DIR}" ]]; then
    log "kimi-datasource plugin already installed at ${PLUGIN_DIR}"
else
    log "Installing kimi-datasource plugin..."
    mkdir -p "${KIMI_HOME}/plugins/managed"
    TMP_DIR=$(mktemp -d)
    curl -fsSL -o "${TMP_DIR}/kimi-datasource.zip" \
        "https://api.kimi.com/coding/v1/marketplace/plugins/kimi-datasource/download"
    unzip -q "${TMP_DIR}/kimi-datasource.zip" -d "${TMP_DIR}/"
    cp -r "${TMP_DIR}/kimi-datasource" "${PLUGIN_DIR}/"
    rm -rf "${TMP_DIR}"
    log "kimi-datasource plugin installed at ${PLUGIN_DIR}"
fi

# 4. Interactive login (first time only)
if [[ ! -f "${KIMI_HOME}/credentials/kimi-code.json" ]]; then
    log "Please complete the browser login. The URL and code will be printed below."
    log "If you cannot open a browser on this server, run 'kimi login' on a machine with a browser, then copy ${KIMI_HOME}/credentials/kimi-code.json to the server."
    kimi login
else
    log "Credentials already exist at ${KIMI_HOME}/credentials/kimi-code.json"
fi

# 5. Install refresh script and systemd timer
REFRESH_SRC="${SCRIPT_DIR}/refresh-token.sh"
REFRESH_DST="${KIMI_HOME}/scripts/refresh-token.sh"
if [[ -f "${REFRESH_SRC}" ]]; then
    mkdir -p "${KIMI_HOME}/scripts"
    cp "${REFRESH_SRC}" "${REFRESH_DST}"
    chmod +x "${REFRESH_DST}"
    log "Refresh script installed at ${REFRESH_DST}"
else
    log "WARN: refresh-token.sh not found at ${REFRESH_SRC}"
fi

SERVICE_SRC="${SCRIPT_DIR}/kimi-token-refresh.service"
TIMER_SRC="${SCRIPT_DIR}/kimi-token-refresh.timer"
if [[ -f "${SERVICE_SRC}" && -f "${TIMER_SRC}" && -f "${REFRESH_DST}" ]]; then
    sed "s|/root/.kimi-code/scripts/refresh-token.sh|${REFRESH_DST}|g" "${SERVICE_SRC}" > /etc/systemd/system/kimi-token-refresh.service
    cp "${TIMER_SRC}" /etc/systemd/system/kimi-token-refresh.timer
    systemctl daemon-reload
    systemctl enable --now kimi-token-refresh.timer
    log "Systemd timer installed: kimi-token-refresh.timer"
else
    log "WARN: systemd service/timer files or refresh script missing; timer not installed"
fi

log "Setup complete."
log "Verify with: kimi login"
log "Verify timer with: systemctl status kimi-token-refresh.timer"
