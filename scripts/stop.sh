#!/usr/bin/env bash
#
# stop.sh — Stop all DeerFlow services
#
# Usage:
#   ./scripts/stop.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

exec "$REPO_ROOT/scripts/serve.sh" --stop
