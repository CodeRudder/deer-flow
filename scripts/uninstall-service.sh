#!/usr/bin/env bash
#
# uninstall-service.sh — Uninstall DeerFlow systemd service
#
# Usage:
#   sudo ./scripts/uninstall-service.sh

set -euo pipefail

SERVICE_NAME="deerflow"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

if [ "$EUID" -ne 0 ]; then
    echo "This script must be run as root (use sudo)."
    exit 1
fi

if [ ! -f "$UNIT_FILE" ]; then
    echo "Service not installed ($UNIT_FILE not found)."
    exit 0
fi

# Stop service if running
if systemctl is-active --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "Stopping $SERVICE_NAME..."
    systemctl stop "$SERVICE_NAME"
fi

# Disable if enabled
if systemctl is-enabled --quiet "$SERVICE_NAME" 2>/dev/null; then
    echo "Disabling $SERVICE_NAME..."
    systemctl disable "$SERVICE_NAME"
fi

# Remove unit file
rm -f "$UNIT_FILE"
systemctl daemon-reload

echo ""
echo "✓ DeerFlow service uninstalled"
echo ""
