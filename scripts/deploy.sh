#!/usr/bin/env bash
#
# deploy.sh - Build, start, or stop DeerFlow production services
#
# Commands:
#   deploy.sh                    — build + start
#   deploy.sh build              — build all images (mode-agnostic)
#   deploy.sh start              — start from pre-built images
#   deploy.sh down               — stop and remove containers
#
# Sandbox mode (local / aio / provisioner) is auto-detected from config.yaml.
#
# Examples:
#   deploy.sh                    # build + start
#   deploy.sh build              # build all images
#   deploy.sh start              # start pre-built images
#   deploy.sh down               # stop and remove containers
#
# Deployment layout (standalone deploy dir, default ~/deer-flow-deploy):
#   deer-flow-deploy/
#   ├── deploy.sh              # this script, staged copy — run start/stop from here
#   ├── repo.env               # records DEER_FLOW_PROJECT_ROOT (the source repo)
#   ├── docker-compose.yaml    # staged from docker/docker-compose.prod.yaml (no
#   │                          # build section; all paths inside the deploy dir)
#   ├── nginx/  skills/        # staged config + runtime mounts
#   ├── config.yaml            # runtime config (seeded from repo on first run)
#   └── data/deer-flow/        # DEER_FLOW_HOME — all runtime data (bind mount)
#
# Repo runs (from a repo checkout) stage all of the above and then operate the
# deploy dir. Images are built repo-side from docker/docker-compose.yaml
# (deer-flow-{frontend,gateway,provisioner}:latest); the prod compose only
# references them by name. The staged copy skips staging, so the deploy dir
# keeps working even if the repo is moved or deleted.

set -e

case "${1:-}" in
    build|start|down)
        CMD="$1"
        if [ -n "${2:-}" ]; then
            echo "Unknown argument: $2"
            echo "Usage: deploy.sh [build|start|down]"
            exit 1
        fi
        ;;
    "")
        CMD=""
        ;;
    *)
        echo "Unknown argument: $1"
        echo "Usage: deploy.sh [build|start|down]"
        exit 1
        ;;
esac

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ── 独立部署目录（standalone deploy dir）──────────────────────────────────────
# 正式环境不依赖仓库工作区存活：compose yml、启停脚本、nginx/provisioner 配置
# 全部复制到 $DEER_FLOW_DEPLOY_DIR（缺省 ~/deer-flow-deploy），数据在其下的
# data/deer-flow 相对子目录。仓库只作为镜像构建源——路径记录在部署目录的
# repo.env（staged deploy.sh 启动时自动 source），仓库移动/更新不影响已部署
# 环境的启停。从部署目录直接运行 `./deploy.sh start|stop` 即可独立操作。
if [ -z "$DEER_FLOW_PROJECT_ROOT" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/repo.env" ]; then
    # staged copy in the deploy dir: recover the repo path recorded at staging time
    # shellcheck disable=SC1091
    source "$(dirname "${BASH_SOURCE[0]}")/repo.env"
    STAGED_DEPLOY=1
fi
export DEER_FLOW_PROJECT_ROOT="${DEER_FLOW_PROJECT_ROOT:-$REPO_ROOT}"
REPO_ROOT="$DEER_FLOW_PROJECT_ROOT"

# 缺省部署目录：staged 副本（同目录有 repo.env）默认就在自己所在目录运行
# （部署目录自包含）；仓库侧运行则用 ~/deer-flow-deploy。显式
# DEER_FLOW_DEPLOY_DIR 始终优先。
if [ -z "$DEER_FLOW_DEPLOY_DIR" ] && [ -f "$(dirname "${BASH_SOURCE[0]}")/repo.env" ]; then
    DEER_FLOW_DEPLOY_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi
DEER_FLOW_DEPLOY_DIR="${DEER_FLOW_DEPLOY_DIR:-$HOME/deer-flow-deploy}"
export DEER_FLOW_DEPLOY_DIR

stage_deploy_dir() {
    mkdir -p "$DEER_FLOW_DEPLOY_DIR/nginx" "$DEER_FLOW_DEPLOY_DIR/data/deer-flow"
    # script-managed files: refresh on every deploy; data/ is never touched here
    # 运行 compose 用 prod 专用文件：路径全部在部署目录内（无仓库引用、无 build 段）
    cp "$DOCKER_DIR/docker-compose.prod.yaml" "$DEER_FLOW_DEPLOY_DIR/docker-compose.yaml"
    for _overlay in "$DOCKER_DIR"/docker-compose.dood.yaml "$DOCKER_DIR"/docker-compose.cli-auth.yaml; do
        [ -e "$_overlay" ] && cp "$_overlay" "$DEER_FLOW_DEPLOY_DIR/$(basename "$_overlay")"
    done
    cp -r "$DOCKER_DIR/nginx/." "$DEER_FLOW_DEPLOY_DIR/nginx/"
    # skills 是运行期挂载内容，整目录复制进部署目录（每次部署刷新）
    if [ -d "$REPO_ROOT/skills" ]; then
        rm -rf "$DEER_FLOW_DEPLOY_DIR/skills"
        cp -r "$REPO_ROOT/skills" "$DEER_FLOW_DEPLOY_DIR/skills"
    fi
    # env 文件：仓库内 .env → 部署目录别名（保持部署目录自包含）
    if [ -f "$REPO_ROOT/frontend/.env" ]; then
        cp "$REPO_ROOT/frontend/.env" "$DEER_FLOW_DEPLOY_DIR/frontend.env"
    else
        : > "$DEER_FLOW_DEPLOY_DIR/frontend.env"
        echo -e "${YELLOW}⚠ frontend/.env 不存在，已生成空的 frontend.env（生产前端需要时再填）${NC}"
    fi
    if [ -f "$REPO_ROOT/.env" ]; then
        cp "$REPO_ROOT/.env" "$DEER_FLOW_DEPLOY_DIR/runtime.env"
    else
        [ -f "$DEER_FLOW_DEPLOY_DIR/runtime.env" ] || : > "$DEER_FLOW_DEPLOY_DIR/runtime.env"
    fi
    cp "${BASH_SOURCE[0]}" "$DEER_FLOW_DEPLOY_DIR/deploy.sh"
    chmod +x "$DEER_FLOW_DEPLOY_DIR/deploy.sh"
    printf 'export DEER_FLOW_PROJECT_ROOT="%s"\nexport DEER_FLOW_STAGED_DEPLOY=1\n' \
        "$REPO_ROOT" > "$DEER_FLOW_DEPLOY_DIR/repo.env"
}

DOCKER_DIR="$REPO_ROOT/docker"
# The staged copy in the deploy dir skips re-staging: the deploy dir must keep
# working standalone even if the repo is moved or deleted.
if [ "${DEER_FLOW_STAGED_DEPLOY:-0}" != "1" ]; then
    stage_deploy_dir
fi
# 运行 compose：一律指向部署目录内的文件（相对路径不出部署目录范围）
COMPOSE_CMD=(docker compose -p deer-flow --project-directory "$DEER_FLOW_DEPLOY_DIR"
             -f "$DEER_FLOW_DEPLOY_DIR/docker-compose.yaml")
# 构建 compose：在仓库侧构建镜像（deer-flow-*:latest），运行 compose 按名引用
BUILD_COMPOSE_CMD=(docker compose -p deer-flow -f "$DOCKER_DIR/docker-compose.yaml")

# ── Colors ────────────────────────────────────────────────────────────────────

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

# ── DEER_FLOW_HOME ────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_HOME" ]; then
    # 正式数据目录：部署目录下的相对路径（独立于仓库与家目录全局路径）
    export DEER_FLOW_HOME="$DEER_FLOW_DEPLOY_DIR/data/deer-flow"
fi
echo -e "${BLUE}DEER_FLOW_HOME=$DEER_FLOW_HOME${NC}"
mkdir -p "$DEER_FLOW_HOME"

# ── DEER_FLOW_REPO_ROOT / DEER_FLOW_HOST_SKILLS_PATH (DooD path translation) ─
# 运行期宿主路径一律指向部署目录（skills 已暂存进去），与仓库解耦。

export DEER_FLOW_REPO_ROOT="$REPO_ROOT"
export DEER_FLOW_HOST_SKILLS_PATH="$DEER_FLOW_DEPLOY_DIR/skills"

# ── config.yaml ───────────────────────────────────────────────────────────────

if [ -z "$DEER_FLOW_CONFIG_PATH" ]; then
    # 配置也放部署目录（独立存活）：首次运行会从仓库 example 播种，之后以这里为准
    export DEER_FLOW_CONFIG_PATH="$DEER_FLOW_DEPLOY_DIR/config.yaml"
fi

if  [ "$CMD" != "down" ] && [ ! -f "$DEER_FLOW_CONFIG_PATH" ]; then
    # Try to seed from repo (config.example.yaml is the canonical template)
    if [ -f "$REPO_ROOT/config.example.yaml" ]; then
        cp "$REPO_ROOT/config.example.yaml" "$DEER_FLOW_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded config.example.yaml → $DEER_FLOW_CONFIG_PATH${NC}"
        echo -e "${YELLOW}⚠ config.yaml was seeded from the example template.${NC}"
        echo "  Run 'make setup' to generate a minimal config, or edit $DEER_FLOW_CONFIG_PATH manually before use."
    else
        echo -e "${RED}✗ No config.yaml found.${NC}"
        echo "  Run 'make setup' from the repo root (recommended),"
        echo "  or 'make config' for the full template, then set the required model API keys."
        exit 1
    fi
else
    echo -e "${GREEN}✓ config.yaml: $DEER_FLOW_CONFIG_PATH${NC}"
fi

# ── extensions_config.json ───────────────────────────────────────────────────

if [ -z "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH="$DEER_FLOW_DEPLOY_DIR/extensions_config.json"
fi

if [ ! -f "$DEER_FLOW_EXTENSIONS_CONFIG_PATH" ]; then
    if [ -f "$REPO_ROOT/extensions_config.json" ]; then
        cp "$REPO_ROOT/extensions_config.json" "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${GREEN}✓ Seeded extensions_config.json → $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    else
        # Create a minimal empty config so the gateway doesn't fail on startup
        echo '{"mcpServers":{},"skills":{}}' > "$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
        echo -e "${YELLOW}⚠ extensions_config.json not found, created empty config at $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
    fi
else
    echo -e "${GREEN}✓ extensions_config.json: $DEER_FLOW_EXTENSIONS_CONFIG_PATH${NC}"
fi


# ── BETTER_AUTH_SECRET ───────────────────────────────────────────────────────
# Required by Next.js in production. Generated once and persisted so auth
# sessions survive container restarts.

_secret_file="$DEER_FLOW_HOME/.better-auth-secret"
if [ -z "$BETTER_AUTH_SECRET" ]; then
    if [ -f "$_secret_file" ]; then
        export BETTER_AUTH_SECRET
        BETTER_AUTH_SECRET="$(cat "$_secret_file")"
        echo -e "${GREEN}✓ BETTER_AUTH_SECRET loaded from $_secret_file${NC}"
    else
        export BETTER_AUTH_SECRET
        if command -v python3 > /dev/null 2>&1 && \
            BETTER_AUTH_SECRET="$(python3 -c 'import sys; sys.version_info >= (3, 6) or sys.exit(1); import secrets; print(secrets.token_hex(32))' 2>/dev/null)"; then
            true
        elif command -v python > /dev/null 2>&1 && \
            BETTER_AUTH_SECRET="$(python -c 'import sys; sys.version_info >= (3, 6) or sys.exit(1); import secrets; print(secrets.token_hex(32))' 2>/dev/null)"; then
            true
        elif command -v openssl > /dev/null 2>&1 && \
            BETTER_AUTH_SECRET="$(openssl rand -hex 32)"; then
            true
        else
            echo -e "${RED}✗ Cannot generate BETTER_AUTH_SECRET: python3, python, and openssl are all unavailable.${NC}" >&2
            echo -e "${RED}  Set BETTER_AUTH_SECRET manually before running make up.${NC}" >&2
            exit 1
        fi
        echo "$BETTER_AUTH_SECRET" > "$_secret_file"
        chmod 600 "$_secret_file"
        echo -e "${GREEN}✓ BETTER_AUTH_SECRET generated → $_secret_file${NC}"
    fi
fi

# ── DEER_FLOW_INTERNAL_AUTH_TOKEN ────────────────────────────────────────────
# Shared by all Gateway workers so channel workers can call internal Gateway
# APIs even when the request is handled by a different Uvicorn worker.

_internal_auth_token_file="$DEER_FLOW_HOME/.internal-auth-token"
if  [ "$CMD" != "down" ] && [ -z "$DEER_FLOW_INTERNAL_AUTH_TOKEN" ]; then
    if [ -f "$_internal_auth_token_file" ]; then
        export DEER_FLOW_INTERNAL_AUTH_TOKEN
        DEER_FLOW_INTERNAL_AUTH_TOKEN="$(cat "$_internal_auth_token_file")"
        echo -e "${GREEN}✓ DEER_FLOW_INTERNAL_AUTH_TOKEN loaded from $_internal_auth_token_file${NC}"
    else
        export DEER_FLOW_INTERNAL_AUTH_TOKEN
        if command -v python3 > /dev/null 2>&1 && \
            DEER_FLOW_INTERNAL_AUTH_TOKEN="$(python3 -c 'import sys; sys.version_info >= (3, 6) or sys.exit(1); import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null)"; then
            true
        elif command -v python > /dev/null 2>&1 && \
            DEER_FLOW_INTERNAL_AUTH_TOKEN="$(python -c 'import sys; sys.version_info >= (3, 6) or sys.exit(1); import secrets; print(secrets.token_urlsafe(32))' 2>/dev/null)"; then
            true
        elif command -v openssl > /dev/null 2>&1 && \
            DEER_FLOW_INTERNAL_AUTH_TOKEN="$(openssl rand -hex 32)"; then
            true
        else
            echo -e "${RED}✗ Cannot generate DEER_FLOW_INTERNAL_AUTH_TOKEN: python3, python, and openssl are all unavailable.${NC}" >&2
            echo -e "${RED}  Set DEER_FLOW_INTERNAL_AUTH_TOKEN manually before running make up.${NC}" >&2
            exit 1
        fi
        echo "$DEER_FLOW_INTERNAL_AUTH_TOKEN" > "$_internal_auth_token_file"
        chmod 600 "$_internal_auth_token_file"
        echo -e "${GREEN}✓ DEER_FLOW_INTERNAL_AUTH_TOKEN generated → $_internal_auth_token_file${NC}"
    fi
fi

# ── detect_sandbox_mode ───────────────────────────────────────────────────────

detect_sandbox_mode() {
    local sandbox_use=""
    local provisioner_url=""

    [ -f "$DEER_FLOW_CONFIG_PATH" ] || { echo "local"; return; }

    sandbox_use=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*use:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*use:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    provisioner_url=$(awk '
        /^[[:space:]]*sandbox:[[:space:]]*$/ { in_sandbox=1; next }
        in_sandbox && /^[^[:space:]#]/ { in_sandbox=0 }
        in_sandbox && /^[[:space:]]*provisioner_url:[[:space:]]*/ {
            line=$0; sub(/^[[:space:]]*provisioner_url:[[:space:]]*/, "", line); print line; exit
        }
    ' "$DEER_FLOW_CONFIG_PATH")

    if [[ "$sandbox_use" == *"deerflow.community.aio_sandbox:AioSandboxProvider"* ]]; then
        if [ -n "$provisioner_url" ]; then
            echo "provisioner"
        else
            echo "aio"
        fi
    else
        echo "local"
    fi
}

# ── down ──────────────────────────────────────────────────────────────────────

if [ "$CMD" = "down" ]; then
    # Set minimal env var defaults so docker compose can parse the prod file
    # without warning about unset variables it interpolates. All defaults
    # mirror docker-compose.prod.yaml (paths stay inside the deploy dir).
    export DEER_FLOW_HOME="${DEER_FLOW_HOME:-$DEER_FLOW_DEPLOY_DIR/data/deer-flow}"
    export DEER_FLOW_HOST_SKILLS_PATH="${DEER_FLOW_HOST_SKILLS_PATH:-$DEER_FLOW_DEPLOY_DIR/skills}"
    export BETTER_AUTH_SECRET="${BETTER_AUTH_SECRET:-placeholder}"
    export DEER_FLOW_INTERNAL_AUTH_TOKEN="${DEER_FLOW_INTERNAL_AUTH_TOKEN:-placeholder}"
    "${COMPOSE_CMD[@]}" down
    exit 0
fi

# ── build ────────────────────────────────────────────────────────────────────
# Build produces mode-agnostic images. No --gateway or sandbox detection needed.

if [ "$CMD" = "build" ]; then
    echo "=========================================="
    echo "  DeerFlow — Building Images"
    echo "=========================================="
    echo ""

    # 构建在仓库侧进行（repo compose 有 build 段，产物 deer-flow-*:latest）；
    # 部署目录的 prod compose 只按名引用这些镜像。
    "${BUILD_COMPOSE_CMD[@]}" build

    echo ""
    echo "=========================================="
    echo "  ✓ Images built successfully"
    echo "=========================================="
    echo ""
    echo "  Next: deploy.sh start"
    echo ""
    exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────

echo "=========================================="
echo "  DeerFlow Production Deployment"
echo "=========================================="
echo ""

# ── Detect runtime configuration ────────────────────────────────────────────
# Only needed for start / up — determines whether provisioner is launched.

sandbox_mode="$(detect_sandbox_mode)"
echo -e "${BLUE}Sandbox mode: $sandbox_mode${NC}"

echo -e "${BLUE}Runtime: Gateway embedded agent runtime${NC}"

services="frontend gateway nginx"

if [ "$sandbox_mode" = "provisioner" ]; then
    services="$services provisioner"
fi

# ── DEER_FLOW_DOCKER_SOCKET (aio / pure-DooD mode only) ──────────────────────
# Only aio mode (AioSandboxProvider without provisioner_url) needs the host
# Docker socket. It is mounted via the opt-in docker-compose.dood.yaml overlay,
# appended here, so the default (local) and provisioner modes never expose the
# host daemon. Mounting the socket = root-equivalent host control; see SECURITY.md.

if [ -z "$DEER_FLOW_DOCKER_SOCKET" ]; then
    export DEER_FLOW_DOCKER_SOCKET="/var/run/docker.sock"
fi

if [ "$sandbox_mode" = "aio" ]; then
    if [ ! -S "$DEER_FLOW_DOCKER_SOCKET" ]; then
        echo -e "${RED}⚠ Docker socket not found at $DEER_FLOW_DOCKER_SOCKET${NC}"
        echo "  AioSandboxProvider (DooD) will not work."
        exit 1
    fi
    echo -e "${GREEN}✓ Docker socket: $DEER_FLOW_DOCKER_SOCKET${NC}"
    echo -e "${YELLOW}  Mounting host Docker socket into gateway (DooD = host root-equivalent). See SECURITY.md.${NC}"
    COMPOSE_CMD+=(-f "$DOCKER_DIR/docker-compose.dood.yaml")
fi

echo ""

# ── Start / Up ───────────────────────────────────────────────────────────────

if [ "$CMD" = "start" ]; then
    echo "Starting containers (no rebuild)..."
    echo ""
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up -d --remove-orphans $services
else
    # Default: build (repo-side) + start (deploy-dir prod compose)
    echo "Building images and starting containers..."
    echo ""
    "${BUILD_COMPOSE_CMD[@]}" build
    # shellcheck disable=SC2086
    "${COMPOSE_CMD[@]}" up -d --remove-orphans $services
fi

echo ""
echo "=========================================="
echo "  DeerFlow is running!"
echo "=========================================="
echo ""
echo "  🌐 Application: http://localhost:${PORT:-2026}"
echo "  📡 API Gateway: http://localhost:${PORT:-2026}/api/*"
echo "  🤖 Runtime:     Gateway embedded"
echo "  API:            /api/langgraph/* → Gateway"
echo ""
echo "  Manage:"
echo "    make down        — stop and remove containers"
echo "    make docker-logs — view logs"
echo ""
