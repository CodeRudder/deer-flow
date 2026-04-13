#!/usr/bin/env bash
#
# start.sh — Start DeerFlow services
#
# Usage:
#   ./scripts/start.sh [mode]
#
# Modes:
#   dev       Development mode with hot-reload (default)
#   prod      Production mode
#   gateway   Gateway mode (no LangGraph server, experimental)
#
# Examples:
#   ./scripts/start.sh            # dev mode, foreground
#   ./scripts/start.sh prod       # prod mode, foreground
#   ./scripts/start.sh gateway    # dev + gateway mode, foreground

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

MODE="${1:-dev}"

case "$MODE" in
    dev)     exec "$REPO_ROOT/scripts/serve.sh" --dev ;;
    prod)    exec "$REPO_ROOT/scripts/serve.sh" --prod ;;
    gateway) exec "$REPO_ROOT/scripts/serve.sh" --dev --gateway ;;
    prod-gateway) exec "$REPO_ROOT/scripts/serve.sh" --prod --gateway ;;
    *)
        echo "Usage: $0 [dev|prod|gateway|prod-gateway]"
        echo ""
        echo "  dev           Development mode with hot-reload (default)"
        echo "  prod          Production mode"
        echo "  gateway       Gateway mode (experimental)"
        echo "  prod-gateway  Production + Gateway mode"
        exit 1
        ;;
esac
