#!/usr/bin/env bash
#
# serve.sh — Unified DeerFlow service launcher
#
# Usage:
#   ./scripts/serve.sh [--dev|--prod] [--gateway] [--daemon] [--stop|--restart]
#
# Modes:
#   --dev       Development mode with hot-reload (default)
#   --prod      Production mode, pre-built frontend, no hot-reload
#   --gateway   Gateway mode (experimental): skip LangGraph server,
#               agent runtime embedded in Gateway API
#   --daemon    Run all services in background (nohup), exit after startup
#
# Actions:
#   --skip-install  Skip dependency installation (faster restart)
#   --stop      Stop all running services and exit
#   --restart   Stop all services, then start with the given mode flags
#
# Examples:
#   ./scripts/serve.sh --dev                 # Standard dev (4 processes)
#   ./scripts/serve.sh --dev --gateway       # Gateway dev  (3 processes)
#   ./scripts/serve.sh --prod --gateway      # Gateway prod (3 processes)
#   ./scripts/serve.sh --dev --daemon        # Standard dev, background
#   ./scripts/serve.sh --dev --gateway --daemon  # Gateway dev, background
#   ./scripts/serve.sh --stop                # Stop all services
#   ./scripts/serve.sh --restart --dev --gateway # Restart in gateway mode
#
# Must be run from the repo root directory.

set -e

REPO_ROOT="$(builtin cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"
cd "$REPO_ROOT"

# ── Load .env ────────────────────────────────────────────────────────────────

if [ -f "$REPO_ROOT/.env" ]; then
    set -a
    source "$REPO_ROOT/.env"
    set +a
fi

# ── Resource limits (cgroup v2) ────────────────────────────────────────────────
# Requires systemd 240+ and cgroup v2.  Detected at runtime; silently skipped
# when unavailable.
#
# Override via environment or .env:
#   DEER_FLOW_CGROUP=0                    # disable cgroup limits entirely
#   LANGGRAPH_MEMORY_MAX=6G               # LangGraph memory cap
#   GATEWAY_MEMORY_MAX=2G                 # Gateway memory cap
#   FRONTEND_MEMORY_MAX=1G                # Frontend memory cap
#   LANGGRAPH_CPU_QUOTA=300%              # LangGraph CPU quota (300% = 3 cores)
#   GATEWAY_CPU_QUOTA=200%                # Gateway CPU quota (200% = 2 cores)
#   FRONTEND_CPU_QUOTA=100%               # Frontend CPU quota (100% = 1 core)
#   LANGGRAPH_IO_MAX=50M                  # LangGraph IO write limit (50 MB/s, 0=unlimited)
#   GATEWAY_IO_MAX=30M                    # Gateway IO write limit (30 MB/s, 0=unlimited)
#   FRONTEND_IO_MAX=10M                   # Frontend IO write limit (10 MB/s, 0=unlimited)

_can_use_cgroup() {
    # Quick check: is cgroup v2 + systemd-run --user available?
    [ "${DEER_FLOW_CGROUP:-1}" = "0" ] && return 1
    [ ! -f /sys/fs/cgroup/cgroup.controllers ] && return 1
    command -v systemd-run >/dev/null 2>&1 || return 1
    # Verify controllers are delegated to user slice
    local controllers
    controllers=$(cat /sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.controllers 2>/dev/null)
    echo "$controllers" | grep -q "memory" || return 1
    return 0
}

# Build a systemd-run prefix for resource-constrained execution.
# IO write limit is applied after scope creation via systemctl set-property
# (avoids shell quoting issues with space-separated device:value in --property).
cgroup_wrap() {
    local name="$1" mem="$2" cpu_quota="${3:-100%}" io_max="${4:-0}"
    echo "systemd-run --user --scope --unit=$name --property=MemoryMax=$mem --property=CPUQuota=$cpu_quota"
}

# Apply IO write bandwidth limit to an already-created scope.
apply_io_limit() {
    local scope_name="$1" io_max="$2"
    [ "$io_max" = "0" ] && return 0
    # Resolve the block device (major:minor) for the filesystem hosting the project
    local dev_source dev_major_minor
    dev_source=$(df --output=source "$REPO_ROOT" 2>/dev/null | tail -1)
    dev_major_minor=$(lsblk -no MAJ:MIN "$dev_source" 2>/dev/null)
    [ -z "$dev_major_minor" ] && return 0
    systemctl --user set-property "$scope_name.scope" "IOWriteBandwidthMax=$dev_major_minor $io_max" 2>/dev/null || true
}

# ── Argument parsing ─────────────────────────────────────────────────────────

DEV_MODE=true
GATEWAY_MODE=false
DAEMON_MODE=false
SKIP_INSTALL=false
ACTION="start"   # start | stop | restart

for arg in "$@"; do
    case "$arg" in
        --dev)     DEV_MODE=true ;;
        --prod)    DEV_MODE=false ;;
        --gateway) GATEWAY_MODE=true ;;
        --daemon)  DAEMON_MODE=true ;;
        --skip-install) SKIP_INSTALL=true ;;
        --stop)    ACTION="stop" ;;
        --restart) ACTION="restart" ;;
        *)
            echo "Unknown argument: $arg"
            echo "Usage: $0 [--dev|--prod] [--gateway] [--daemon] [--skip-install] [--stop|--restart]"
            exit 1
            ;;
    esac
done

# ── Stop helper ──────────────────────────────────────────────────────────────

stop_all() {
    echo "Stopping all services..."
    pkill -f "langgraph dev" 2>/dev/null || true
    pkill -f "uvicorn app.gateway.app:app" 2>/dev/null || true
    pkill -f "next dev" 2>/dev/null || true
    pkill -f "next start" 2>/dev/null || true
    pkill -f "next-server" 2>/dev/null || true
    nginx -c "$REPO_ROOT/docker/nginx/nginx.local.conf" -p "$REPO_ROOT" -s quit 2>/dev/null || true
    sleep 1
    pkill -9 nginx 2>/dev/null || true
    ./scripts/cleanup-containers.sh deer-flow-sandbox 2>/dev/null || true
    echo "✓ All services stopped"
}

# ── Action routing ───────────────────────────────────────────────────────────

if [ "$ACTION" = "stop" ]; then
    stop_all
    exit 0
fi

ALREADY_STOPPED=false
if [ "$ACTION" = "restart" ]; then
    stop_all
    sleep 1
    ALREADY_STOPPED=true
fi

# ── Derive runtime flags ────────────────────────────────────────────────────

if $GATEWAY_MODE; then
    export SKIP_LANGGRAPH_SERVER=1
fi

# Mode label for banner
if $DEV_MODE && $GATEWAY_MODE; then
    MODE_LABEL="DEV + GATEWAY (experimental)"
elif $DEV_MODE; then
    MODE_LABEL="DEV (hot-reload enabled)"
elif $GATEWAY_MODE; then
    MODE_LABEL="PROD + GATEWAY (experimental)"
else
    MODE_LABEL="PROD (optimized)"
fi

if $DAEMON_MODE; then
    MODE_LABEL="$MODE_LABEL [daemon]"
fi

# Frontend command
FRONTEND_PORT=2025
if $DEV_MODE; then
    FRONTEND_CMD="PORT=$FRONTEND_PORT pnpm run dev"
else
    if command -v python3 >/dev/null 2>&1; then
        PYTHON_BIN="python3"
    elif command -v python >/dev/null 2>&1; then
        PYTHON_BIN="python"
    else
        echo "Python is required to generate BETTER_AUTH_SECRET."
        exit 1
    fi
    FRONTEND_CMD="PORT=$FRONTEND_PORT env BETTER_AUTH_SECRET=$($PYTHON_BIN -c 'import secrets; print(secrets.token_hex(16))') pnpm run start"
fi

# Extra flags for uvicorn/langgraph
LANGGRAPH_EXTRA_FLAGS="--no-reload"
if $DEV_MODE && ! $DAEMON_MODE; then
    GATEWAY_EXTRA_FLAGS="--reload --reload-include='*.yaml' --reload-include='.env' --reload-exclude='*.pyc' --reload-exclude='__pycache__' --reload-exclude='sandbox/' --reload-exclude='.deer-flow/'"
else
    GATEWAY_EXTRA_FLAGS=""
fi

# ── Stop existing services (skip if restart already did it) ──────────────────

if ! $ALREADY_STOPPED; then
    stop_all
    sleep 1
fi

# ── Config check ─────────────────────────────────────────────────────────────

if ! { \
        [ -n "$DEER_FLOW_CONFIG_PATH" ] && [ -f "$DEER_FLOW_CONFIG_PATH" ] || \
        [ -f backend/config.yaml ] || \
        [ -f config.yaml ]; \
    }; then
    echo "✗ No DeerFlow config file found."
    echo "  Run 'make config' to generate config.yaml."
    exit 1
fi

"$REPO_ROOT/scripts/config-upgrade.sh"

# ── Install dependencies ────────────────────────────────────────────────────

if ! $SKIP_INSTALL; then
    echo "Syncing dependencies..."
    (cd backend && uv sync --quiet) || { echo "✗ Backend dependency install failed"; exit 1; }
    (cd frontend && pnpm install --silent) || { echo "✗ Frontend dependency install failed"; exit 1; }
    echo "✓ Dependencies synced"
else
    echo "⏩ Skipping dependency install (--skip-install)"
fi

# ── Sync frontend .env.local ─────────────────────────────────────────────────
# Next.js .env.local takes precedence over process env vars.
# The script manages the NEXT_PUBLIC_LANGGRAPH_BASE_URL line to ensure
# the frontend routes match the active backend mode.

FRONTEND_ENV_LOCAL="$REPO_ROOT/frontend/.env.local"
ENV_KEY="NEXT_PUBLIC_LANGGRAPH_BASE_URL"

sync_frontend_env() {
    if $GATEWAY_MODE; then
        # Point frontend to Gateway's compat API
        if [ -f "$FRONTEND_ENV_LOCAL" ] && grep -q "^${ENV_KEY}=" "$FRONTEND_ENV_LOCAL"; then
            sed -i.bak "s|^${ENV_KEY}=.*|${ENV_KEY}=/api/langgraph-compat|" "$FRONTEND_ENV_LOCAL" && rm -f "${FRONTEND_ENV_LOCAL}.bak"
        else
            echo "${ENV_KEY}=/api/langgraph-compat" >> "$FRONTEND_ENV_LOCAL"
        fi
    else
        # Remove override — frontend falls back to /api/langgraph (standard)
        if [ -f "$FRONTEND_ENV_LOCAL" ] && grep -q "^${ENV_KEY}=" "$FRONTEND_ENV_LOCAL"; then
            sed -i.bak "/^${ENV_KEY}=/d" "$FRONTEND_ENV_LOCAL" && rm -f "${FRONTEND_ENV_LOCAL}.bak"
        fi
    fi
}

sync_frontend_env

# ── Banner ───────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  Starting DeerFlow"
echo "=========================================="
echo ""
echo "  Mode: $MODE_LABEL"
echo ""
echo "  Services:"
if ! $GATEWAY_MODE; then
    echo "    LangGraph   → localhost:2024  (agent runtime)"
fi
echo "    Gateway     → localhost:8001  (REST API$(if $GATEWAY_MODE; then echo " + agent runtime"; fi))"
echo "    Frontend    → localhost:$FRONTEND_PORT  (Next.js)"
echo "    Nginx       → localhost:2026  (reverse proxy)"
echo ""

# ── Cleanup handler ──────────────────────────────────────────────────────────

cleanup() {
    trap - INT TERM
    echo ""
    stop_all
    exit 0
}

trap cleanup INT TERM

# ── Helper: start a service ──────────────────────────────────────────────────

# run_service NAME COMMAND PORT TIMEOUT [CGROUP_ARGS]
# CGROUP_ARGS: "NAME MEMORY_MAX CPU_WEIGHT IO_WEIGHT" (optional, enables cgroup limits)
# In daemon mode, wraps with nohup. Waits for port to be ready.
run_service() {
    local name="$1" cmd="$2" port="$3" timeout="$4"
    local cgroup_args="${5:-}"

    local cgroup_prefix=""
    if [ -n "$cgroup_args" ] && _can_use_cgroup; then
        # shellcheck disable=SC2086
        cgroup_prefix=$(cgroup_wrap $cgroup_args)
        echo "Starting $name (cgroup: memory=$(echo "$cgroup_args" | awk '{print $2}'), cpu=$(echo "$cgroup_args" | awk '{print $3}'), io=$(echo "$cgroup_args" | awk '{print $4}')/s)..."
    else
        echo "Starting $name..."
    fi

    if [ -n "$cgroup_prefix" ]; then
        # systemd-run must be the outermost process for cgroup to take effect.
        # It runs sh -c internally, redirecting to the log file.
        local logfile="logs/$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-').log"
        if $DAEMON_MODE; then
            nohup $cgroup_prefix sh -c "$cmd" > "$logfile" 2>&1 &
        else
            $cgroup_prefix sh -c "$cmd" > "$logfile" 2>&1 &
        fi
    else
        if $DAEMON_MODE; then
            nohup sh -c "$cmd" > /dev/null 2>&1 &
        else
            sh -c "$cmd" &
        fi
    fi

    ./scripts/wait-for-port.sh "$port" "$timeout" "$name" || {
        local logfile="logs/$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr ' ' '-').log"
        echo "✗ $name failed to start."
        [ -f "$logfile" ] && tail -20 "$logfile"
        cleanup
    }

    # Apply IO bandwidth limit after scope is created
    if [ -n "$cgroup_args" ]; then
        local scope_name io_max
        scope_name=$(echo "$cgroup_args" | awk '{print $1}')
        io_max=$(echo "$cgroup_args" | awk '{print $4}')
        apply_io_limit "$scope_name" "$io_max"
    fi

    echo "✓ $name started on localhost:$port"
}

# ── Start services ───────────────────────────────────────────────────────────

mkdir -p logs
mkdir -p temp/client_body_temp temp/proxy_temp temp/fastcgi_temp temp/uwsgi_temp temp/scgi_temp

# Pre-create DeerFlow base directories to avoid blocking mkdir during async request handling
mkdir -p backend/.deer-flow/threads
mkdir -p backend/.deer-flow/skills/custom
mkdir -p backend/.deer-flow/memory
mkdir -p backend/.deer-flow/acp-workspace

# 1. LangGraph (skip in gateway mode)
if ! $GATEWAY_MODE; then
    CONFIG_LOG_LEVEL=$(grep -m1 '^log_level:' config.yaml 2>/dev/null | awk '{print $2}' | tr -d ' ')
    LANGGRAPH_LOG_LEVEL="${LANGGRAPH_LOG_LEVEL:-${CONFIG_LOG_LEVEL:-info}}"
    LANGGRAPH_JOBS_PER_WORKER="${LANGGRAPH_JOBS_PER_WORKER:-10}"
    LANGGRAPH_ALLOW_BLOCKING="${LANGGRAPH_ALLOW_BLOCKING:-0}"
    LANGGRAPH_ALLOW_BLOCKING_FLAG=""
    if [ "$LANGGRAPH_ALLOW_BLOCKING" = "1" ]; then
        LANGGRAPH_ALLOW_BLOCKING_FLAG="--allow-blocking"
    fi
    run_service "LangGraph" \
        "cd backend && NO_COLOR=1 uv run langgraph dev --no-browser $LANGGRAPH_ALLOW_BLOCKING_FLAG --n-jobs-per-worker $LANGGRAPH_JOBS_PER_WORKER --server-log-level $LANGGRAPH_LOG_LEVEL $LANGGRAPH_EXTRA_FLAGS > ../logs/langgraph.log 2>&1" \
        2024 60 \
        "deerflow-langgraph ${LANGGRAPH_MEMORY_MAX:-6G} ${LANGGRAPH_CPU_QUOTA:-300%} ${LANGGRAPH_IO_MAX:-50M}"
else
    echo "⏩ Skipping LangGraph (Gateway mode — runtime embedded in Gateway)"
fi

# 2. Gateway API
run_service "Gateway" \
    "cd backend && PYTHONPATH=. uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 $GATEWAY_EXTRA_FLAGS > ../logs/gateway.log 2>&1" \
    8001 30 \
    "deerflow-gateway ${GATEWAY_MEMORY_MAX:-2G} ${GATEWAY_CPU_QUOTA:-200%} ${GATEWAY_IO_MAX:-30M}"

# 3. Frontend
run_service "Frontend" \
    "cd frontend && $FRONTEND_CMD > ../logs/frontend.log 2>&1" \
    $FRONTEND_PORT 120 \
    "deerflow-frontend ${FRONTEND_MEMORY_MAX:-1G} ${FRONTEND_CPU_QUOTA:-100%} ${FRONTEND_IO_MAX:-10M}"

# 4. Nginx
run_service "Nginx" \
    "nginx -g 'daemon off;' -c '$REPO_ROOT/docker/nginx/nginx.local.conf' -p '$REPO_ROOT' > logs/nginx.log 2>&1" \
    2026 10

# ── Ready ────────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "  ✓ DeerFlow is running!  [$MODE_LABEL]"
echo "=========================================="
echo ""
echo "  🌐 http://localhost:2026"
echo ""
if $GATEWAY_MODE; then
    echo "  Routing: Frontend → Nginx → Gateway (embedded runtime)"
    echo "  API:     /api/langgraph-compat/*  →  Gateway agent runtime"
else
    echo "  Routing: Frontend → Nginx → LangGraph + Gateway"
    echo "  API:     /api/langgraph/*  →  LangGraph server (2024)"
fi
echo "           /api/*              →  Gateway REST API (8001)"
echo ""
echo "  📋 Logs: logs/{langgraph,gateway,frontend,nginx}.log"
echo ""
if _can_use_cgroup; then
    echo "  cgroup limits: LangGraph=${LANGGRAPH_MEMORY_MAX:-6G}/${LANGGRAPH_CPU_QUOTA:-300%}/${LANGGRAPH_IO_MAX:-50M} Gateway=${GATEWAY_MEMORY_MAX:-2G}/${GATEWAY_CPU_QUOTA:-200%}/${GATEWAY_IO_MAX:-30M} Frontend=${FRONTEND_MEMORY_MAX:-1G}/${FRONTEND_CPU_QUOTA:-100%}/${FRONTEND_IO_MAX:-10M}"
    echo ""
fi

if $DAEMON_MODE; then
    echo "  🛑 Stop: make stop"
    # Detach — trap is no longer needed
    trap - INT TERM
else
    echo "  Press Ctrl+C to stop all services"
    wait
fi
