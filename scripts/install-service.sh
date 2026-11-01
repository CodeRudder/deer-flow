#!/usr/bin/env bash
#
# install-service.sh — Install DeerFlow as a systemd service
#
# Usage:
#   ./scripts/install-service.sh [mode] [user]
#
# Modes:
#   dev       Development mode (default)
#   prod      Production mode
#   gateway   Gateway mode
#
# Examples:
#   sudo ./scripts/install-service.sh                  # prod mode, current user
#   sudo ./scripts/install-service.sh dev              # dev mode
#   sudo ./scripts/install-service.sh prod otheruser   # prod mode, specific user
#
# After install:
#   sudo systemctl start deerflow
#   sudo systemctl status deerflow
#   sudo systemctl enable deerflow   # auto-start on boot

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-prod}"
# When run with sudo, use the real user (not root)
SERVICE_USER="${2:-${SUDO_USER:-$(whoami)}}"
SERVICE_NAME="deerflow"

# Build serve.sh flags from mode
case "$MODE" in
    dev)          SERVE_FLAGS="--dev --daemon --skip-install" ;;
    prod)         SERVE_FLAGS="--prod --daemon --skip-install" ;;
    gateway)      SERVE_FLAGS="--dev --gateway --daemon --skip-install" ;;
    prod-gateway) SERVE_FLAGS="--prod --gateway --daemon --skip-install" ;;
    *)
        echo "Usage: sudo $0 [dev|prod|gateway|prod-gateway] [user]"
        exit 1
        ;;
esac

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (use sudo)."
    exit 1
fi

# Detect paths
SERVE_SH="$REPO_ROOT/scripts/serve.sh"
if [ ! -x "$SERVE_SH" ]; then
    chmod +x "$SERVE_SH"
fi

# Build PATH from the service user's actual tool locations
# Probe common locations for node/pnpm/uv
USER_HOME="/home/$SERVICE_USER"
EXTRA_PATHS=""
for dir in \
    "$USER_HOME/.local/bin" \
    "$USER_HOME/.cargo/bin" \
    $(ls -d "$USER_HOME/.config/nvm/versions/node/"*/bin 2>/dev/null) \
    $(ls -d "$USER_HOME/.nvm/versions/node/"*/bin 2>/dev/null) \
; do
    if [ -d "$dir" ] && echo ":$EXTRA_PATHS:" | grep -qv ":$dir:"; then
        EXTRA_PATHS="${EXTRA_PATHS:+$EXTRA_PATHS:}$dir"
    fi
done
RESOLVED_PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin${EXTRA_PATHS:+:$EXTRA_PATHS}"

# Ensure log directory exists
mkdir -p "$REPO_ROOT/logs"
chown "$SERVICE_USER:$SERVICE_USER" "$REPO_ROOT/logs" 2>/dev/null || true

# Create systemd unit file
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

cat > "$UNIT_FILE" << EOF
[Unit]
Description=DeerFlow AI Agent Service ($MODE mode)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=300

[Service]
Type=forking
User=$SERVICE_USER
Group=$(id -gn "$SERVICE_USER")
WorkingDirectory=$REPO_ROOT
Environment=PATH=$RESOLVED_PATH
EnvironmentFile=-$REPO_ROOT/.env

# serve.sh --daemon handles its own stop-before-start, no need for ExecStartPre
ExecStart=$REPO_ROOT/scripts/serve.sh $SERVE_FLAGS
ExecStop=$REPO_ROOT/scripts/serve.sh --stop

# Frontend (next dev) can take 2+ minutes to compile on first start
TimeoutStartSec=300
TimeoutStopSec=30

# Wait for main port to confirm startup (runs after serve.sh exits)
ExecStartPost=$REPO_ROOT/scripts/wait-port.sh

Restart=on-failure
RestartSec=10
StartLimitBurst=5

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=$SERVICE_NAME

[Install]
WantedBy=multi-user.target
EOF

# Reload systemd
systemctl daemon-reload

echo ""
echo "==========================================="
echo "  DeerFlow service installed"
echo "==========================================="
echo ""
echo "  Unit file: $UNIT_FILE"
echo "  Mode:      $MODE"
echo "  User:      $SERVICE_USER"
echo ""
echo "  Commands:"
echo "    sudo systemctl start $SERVICE_NAME     # Start"
echo "    sudo systemctl stop $SERVICE_NAME      # Stop"
echo "    sudo systemctl restart $SERVICE_NAME   # Restart"
echo "    sudo systemctl status $SERVICE_NAME    # Status"
echo "    sudo systemctl enable $SERVICE_NAME    # Auto-start on boot"
echo "    sudo systemctl disable $SERVICE_NAME   # Disable auto-start"
echo "    journalctl -u $SERVICE_NAME -f         # View logs"
echo ""
