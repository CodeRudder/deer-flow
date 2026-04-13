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
SERVICE_USER="${2:-$(whoami)}"
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

[Service]
Type=forking
User=$SERVICE_USER
Group=$(id -gn "$SERVICE_USER")
WorkingDirectory=$REPO_ROOT
Environment=PATH=/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$HOME/.cargo/bin
EnvironmentFile=-$REPO_ROOT/.env

ExecStartPre=$REPO_ROOT/scripts/serve.sh --stop
ExecStart=$REPO_ROOT/scripts/serve.sh $SERVE_FLAGS
ExecStop=$REPO_ROOT/scripts/serve.sh --stop

# Wait for main port to confirm startup
ExecStartPost=/bin/bash -c 'for i in $(seq 1 60); do ss -ltn "( sport = :2026 )" | grep -q . && exit 0; sleep 1; done; exit 1'

Restart=on-failure
RestartSec=10
StartLimitIntervalSec=300
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
