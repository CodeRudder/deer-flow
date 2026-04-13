#!/usr/bin/env bash
#
# restart.sh — Restart DeerFlow services
#
# Usage:
#   ./scripts/restart.sh [mode]
#
# Modes: same as start.sh (dev|prod|gateway|prod-gateway)
#
# Examples:
#   ./scripts/restart.sh            # restart in dev mode
#   ./scripts/restart.sh prod       # restart in prod mode

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-dev}"

case "$MODE" in
    dev)     exec "$REPO_ROOT/scripts/serve.sh" --restart --dev ;;
    prod)    exec "$REPO_ROOT/scripts/serve.sh" --restart --prod ;;
    gateway) exec "$REPO_ROOT/scripts/serve.sh" --restart --dev --gateway ;;
    prod-gateway) exec "$REPO_ROOT/scripts/serve.sh" --restart --prod --gateway ;;
    *)
        echo "Usage: $0 [dev|prod|gateway|prod-gateway]"
        exit 1
        ;;
esac
