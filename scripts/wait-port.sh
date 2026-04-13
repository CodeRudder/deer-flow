#!/usr/bin/env bash
# Wait for DeerFlow main port (2026) to become available.
# Used by systemd ExecStartPost to confirm startup.
set -euo pipefail

PORT="${1:-2026}"
TIMEOUT="${2:-180}"

for i in $(seq 1 "$TIMEOUT"); do
    if ss -ltnH "( sport = :$PORT )" 2>/dev/null | grep -q .; then
        exit 0
    fi
    sleep 1
done
echo "Port $PORT not available after ${TIMEOUT}s" >&2
exit 1
