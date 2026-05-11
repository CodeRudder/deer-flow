#!/usr/bin/env bash
#
# Stop only the web-facing services used during split-process debugging.
# This does not stop LangGraph or Gateway debug processes.
#
# Usage:
#   ./scripts/debug-web-stop.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_PID_FILE="$REPO_ROOT/logs/frontend.pid"
NGINX_CONF="$REPO_ROOT/docker/nginx/nginx.local.conf"

usage() {
    cat <<EOF
Usage:
  ./scripts/debug-web-stop.sh

Stops only the web-facing services used during split-process debugging:
  - Frontend
  - Nginx reverse proxy

This script does not stop PyCharm Debug processes:
  - LangGraph on localhost:2024
  - Gateway on localhost:8001
EOF
}

case "${1:-}" in
    -h|--help)
        usage
        exit 0
        ;;
    "")
        ;;
    *)
        echo "Unknown argument: $1"
        echo ""
        usage
        exit 1
        ;;
esac

stop_pid_file() {
    local name="$1"
    local pid_file="$2"

    if [ ! -f "$pid_file" ]; then
        echo "$name is not running (no pid file)"
        return
    fi

    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        echo "$name is not running"
        rm -f "$pid_file"
        return
    fi

    echo "Stopping $name (pid $pid)..."
    kill "$pid" 2>/dev/null || true

    for _ in 1 2 3 4 5; do
        kill -0 "$pid" 2>/dev/null || break
        sleep 1
    done

    if kill -0 "$pid" 2>/dev/null; then
        echo "$name did not exit cleanly; killing..."
        kill -9 "$pid" 2>/dev/null || true
    fi

    rm -f "$pid_file"
}

echo "Stopping split-debug web services..."

stop_pid_file "Frontend" "$FRONTEND_PID_FILE"

echo "Stopping Nginx..."
nginx -c "$NGINX_CONF" -p "$REPO_ROOT" -s quit 2>/dev/null || true

echo "Done."
