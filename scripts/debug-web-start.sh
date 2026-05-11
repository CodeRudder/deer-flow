#!/usr/bin/env bash
#
# Start only the web-facing services used during split-process debugging.
#
# Expected separately:
#   - LangGraph debug process on 2024
#   - Gateway debug process on 8001
#
# This script starts:
#   - Frontend on 2025
#   - Nginx reverse proxy on 2026
#
# Usage:
#   ./scripts/debug-web-start.sh
#   FRONTEND_PORT=3025 ./scripts/debug-web-start.sh
#   FRONTEND_HOST=0.0.0.0 ./scripts/debug-web-start.sh
#
# Open:
#   http://localhost:2026
#
# Stop:
#   ./scripts/debug-web-stop.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_PORT="${FRONTEND_PORT:-2025}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"

FRONTEND_PID_FILE="$REPO_ROOT/logs/frontend.pid"
NGINX_PID_FILE="$REPO_ROOT/logs/nginx.pid"
FRONTEND_LOG="$REPO_ROOT/logs/frontend.log"
NGINX_LOG="$REPO_ROOT/logs/nginx.log"
NGINX_CONF="$REPO_ROOT/docker/nginx/nginx.local.conf"

usage() {
    cat <<EOF
Usage:
  ./scripts/debug-web-start.sh

Starts only the web-facing services for split-process debugging:
  - Frontend on 127.0.0.1:2025
  - Nginx reverse proxy on localhost:2026

Start these separately in PyCharm Debug first:
  - LangGraph on localhost:2024
  - Gateway on localhost:8001

Environment overrides:
  FRONTEND_PORT=3025     Change frontend port (default: 2025)
  FRONTEND_HOST=0.0.0.0  Change frontend bind host (default: 127.0.0.1)

Open:
  http://localhost:2026

Logs:
  logs/frontend.log
  logs/nginx.log
  logs/nginx-access.log
  logs/nginx-error.log

Stop:
  ./scripts/debug-web-stop.sh
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

is_running() {
    local pid_file="$1"
    [ -f "$pid_file" ] || return 1
    local pid
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    [ -n "$pid" ] || return 1
    kill -0 "$pid" 2>/dev/null
}

wait_for_port() {
    local port="$1"
    local timeout="$2"
    local service="$3"
    local pid_file="${4:-}"
    local logfile="${5:-}"
    local elapsed=0

    while ! "$REPO_ROOT/scripts/wait-for-port.sh" "$port" 1 "$service" >/dev/null 2>&1; do
        if [ -n "$pid_file" ] && ! is_running "$pid_file"; then
            echo ""
            echo "$service exited before opening port $port."
            [ -n "$logfile" ] && [ -f "$logfile" ] && tail -40 "$logfile"
            exit 1
        fi
        if [ "$elapsed" -ge "$timeout" ]; then
            echo ""
            echo "$service failed to start on port $port after ${timeout}s."
            [ -n "$logfile" ] && [ -f "$logfile" ] && tail -40 "$logfile"
            exit 1
        fi
        printf "\r  Waiting for %s on port %s... %ds" "$service" "$port" "$elapsed"
        sleep 1
        elapsed=$((elapsed + 1))
    done

    printf "\r  %-60s\r" ""
}

mkdir -p "$REPO_ROOT/logs"
mkdir -p "$REPO_ROOT/temp/client_body_temp" \
         "$REPO_ROOT/temp/proxy_temp" \
         "$REPO_ROOT/temp/fastcgi_temp" \
         "$REPO_ROOT/temp/uwsgi_temp" \
         "$REPO_ROOT/temp/scgi_temp"

echo "Starting split-debug web services..."

if is_running "$FRONTEND_PID_FILE"; then
    echo "Frontend already running on port $FRONTEND_PORT (pid $(cat "$FRONTEND_PID_FILE"))"
else
    rm -f "$FRONTEND_PID_FILE"
    echo "Starting Frontend on $FRONTEND_HOST:$FRONTEND_PORT..."
    (
        cd "$REPO_ROOT/frontend"
        nohup pnpm exec next dev --turbo --hostname "$FRONTEND_HOST" --port "$FRONTEND_PORT" > "$FRONTEND_LOG" 2>&1 &
        echo $! > "$FRONTEND_PID_FILE"
    )
    wait_for_port "$FRONTEND_PORT" 120 "Frontend" "$FRONTEND_PID_FILE" "$FRONTEND_LOG"
    echo "Frontend started (pid $(cat "$FRONTEND_PID_FILE"))"
fi

if is_running "$NGINX_PID_FILE"; then
    echo "Nginx already running on port 2026 (pid $(cat "$NGINX_PID_FILE"))"
else
    rm -f "$NGINX_PID_FILE"
    echo "Starting Nginx on port 2026..."
    nohup nginx -g "daemon off;" -c "$NGINX_CONF" -p "$REPO_ROOT" > "$NGINX_LOG" 2>&1 &
    echo $! > "$NGINX_PID_FILE"
    wait_for_port 2026 10 "Nginx" "$NGINX_PID_FILE" "$NGINX_LOG"
    echo "Nginx started (pid $(cat "$NGINX_PID_FILE"))"
fi

echo ""
echo "Web entry: http://localhost:2026"
echo "Logs:"
echo "  $FRONTEND_LOG"
echo "  $NGINX_LOG"
echo "  $REPO_ROOT/logs/nginx-access.log"
echo "  $REPO_ROOT/logs/nginx-error.log"
