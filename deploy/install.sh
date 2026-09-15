#!/usr/bin/env bash
#
# install.sh — interactieve uitrol van de GitHub Workflow Fix daemon op de box.
#
# Wat het doet:
#   1. Kopieert dit project naar /root/gh-workflow-fix
#   2. Maakt een venv aan en installeert het pakket (pip install -e '.[dev]')
#   3. Vraagt interactief de env-variabelen op en schrijft /etc/gh-workflow-fix.env
#   4. Installeert de systemd-unit en start de daemon (enable --now)
#   5. Toont de healthz-check
#
# Vereisten: root-rechten, python3.11, systemd. Draaien vanuit de repo-checkout:
#   sudo bash deploy/install.sh
set -euo pipefail

INSTALL_DIR="/root/gh-workflow-fix"
ENV_FILE="/etc/gh-workflow-fix.env"
UNIT_NAME="gh-workflow-fix-daemon"
UNIT_SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/gh-workflow-fix-daemon.service"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "Fout: draai dit script als root (sudo bash deploy/install.sh)" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# Interactieve prompt: $1=naam, $2=default ("" = geen), $3=verplicht (true/false)
# ---------------------------------------------------------------------------
prompt() {
    local name="$1" default="$2" required="$3" value=""
    while true; do
        if [[ -n "$default" ]]; then
            read -r -p "$name [$default]: " value
            value="${value:-$default}"
        else
            read -r -p "$name: " value
        fi
        if [[ -n "$value" ]]; then
            break
        fi
        if [[ "$required" == "true" ]]; then
            echo "  (verplicht — geef een waarde)" >&2
        else
            break
        fi
    done
    printf '%s' "$value"
}

# ---------------------------------------------------------------------------
# 1. Project kopiëren naar /root/gh-workflow-fix
# ---------------------------------------------------------------------------
echo "==> Project kopiëren naar $INSTALL_DIR"
mkdir -p "$INSTALL_DIR"
if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
        --exclude '.git' --exclude '.venv' --exclude '__pycache__' \
        --exclude '.pytest_cache' --exclude '.ruff_cache' \
        "$REPO_ROOT/" "$INSTALL_DIR/"
else
    cp -a "$REPO_ROOT/." "$INSTALL_DIR/"
    rm -rf "$INSTALL_DIR/.git" "$INSTALL_DIR/.venv"
fi

# ---------------------------------------------------------------------------
# 2. venv + installatie
# ---------------------------------------------------------------------------
echo "==> venv aanmaken en pakket installeren"
cd "$INSTALL_DIR"
python3.11 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# ---------------------------------------------------------------------------
# 3. Env-variabelen opvragen en /etc/gh-workflow-fix.env schrijven
# ---------------------------------------------------------------------------
echo "==> Env-variabelen opvragen (schrijft $ENV_FILE)"
GH_TOKEN="$(prompt "GH_TOKEN" "" "true")"
GH_REPO="$(prompt "GH_REPO" "Cerios-TechLab/github-workflow-opencode-fix-and-retry-mcp-server" "true")"
GH_OC_AUTO="$(prompt "GH_OC_AUTO" "true" "true")"
WEBHOOK_SECRET="$(prompt "WEBHOOK_SECRET" "" "true")"
GH_API_BASE="$(prompt "GH_API_BASE" "https://api.github.com" "false")"
WEBHOOK_HOST="$(prompt "WEBHOOK_HOST" "0.0.0.0" "false")"
WEBHOOK_PORT="$(prompt "WEBHOOK_PORT" "18080" "false")"
DATA_DIR="$(prompt "DATA_DIR" "/var/lib/gh-workflow-fix" "false")"
SELF_HEAL_MARKER="$(prompt "SELF_HEAL_MARKER" "# self-heal: true" "false")"
RETRY_DELAYS_MIN="$(prompt "RETRY_DELAYS_MIN" "15,30,60" "false")"
OPENCODE_BIN="$(prompt "OPENCODE_BIN" "/root/.opencode/bin/opencode" "false")"
FIX_TIMEOUT_S="$(prompt "FIX_TIMEOUT_S" "1800" "false")"
SCHEDULER_TICK_S="$(prompt "SCHEDULER_TICK_S" "60" "false")"

umask 077
printf 'GH_TOKEN=%s\n' "$GH_TOKEN" > "$ENV_FILE"
printf 'GH_REPO=%s\n' "$GH_REPO" >> "$ENV_FILE"
printf 'GH_OC_AUTO=%s\n' "$GH_OC_AUTO" >> "$ENV_FILE"
printf 'WEBHOOK_SECRET=%s\n' "$WEBHOOK_SECRET" >> "$ENV_FILE"
printf 'GH_API_BASE=%s\n' "$GH_API_BASE" >> "$ENV_FILE"
printf 'WEBHOOK_HOST=%s\n' "$WEBHOOK_HOST" >> "$ENV_FILE"
printf 'WEBHOOK_PORT=%s\n' "$WEBHOOK_PORT" >> "$ENV_FILE"
printf 'DATA_DIR=%s\n' "$DATA_DIR" >> "$ENV_FILE"
printf 'SELF_HEAL_MARKER=%s\n' "$SELF_HEAL_MARKER" >> "$ENV_FILE"
printf 'RETRY_DELAYS_MIN=%s\n' "$RETRY_DELAYS_MIN" >> "$ENV_FILE"
printf 'OPENCODE_BIN=%s\n' "$OPENCODE_BIN" >> "$ENV_FILE"
printf 'FIX_TIMEOUT_S=%s\n' "$FIX_TIMEOUT_S" >> "$ENV_FILE"
printf 'SCHEDULER_TICK_S=%s\n' "$SCHEDULER_TICK_S" >> "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "    -> $ENV_FILE geschreven (rechten 600)"

# ---------------------------------------------------------------------------
# 4. systemd-unit installeren en daemon starten
# ---------------------------------------------------------------------------
echo "==> systemd-unit installeren en daemon starten"
cp "$UNIT_SRC" "/etc/systemd/system/$UNIT_NAME.service"
systemctl daemon-reload
systemctl enable --now "$UNIT_NAME"

# ---------------------------------------------------------------------------
# 5. Healthz-check
# ---------------------------------------------------------------------------
echo "==> Healthz-check (curl -s localhost:18080/healthz)"
sleep 2
curl -s "localhost:${WEBHOOK_PORT}/healthz"
echo

echo "Klaar. Status: systemctl status $UNIT_NAME"
echo "Let op: open poort ${WEBHOOK_PORT} in firewalld indien van buiten bereikbaar moet zijn:"
echo "  firewall-cmd --permanent --add-port=${WEBHOOK_PORT}/tcp && firewall-cmd --reload"