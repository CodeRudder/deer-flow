#!/usr/bin/env bash
#
# dev-docker.sh — DeerFlow 开发环境 Docker 管理脚本
#
# 与生产实例（deer-flow/）完全隔离：
#   - Docker project name: deer-flow-dev
#   - 外部端口: 2027（生产占用 2026）
#   - 数据目录: ~/.deer-flow-dev
#
# 用法:
#   ./scripts/dev-docker.sh start    — 构建并启动（首次或代码变更后）
#   ./scripts/dev-docker.sh stop     — 停止所有容器
#   ./scripts/dev-docker.sh restart  — 重启所有容器（不重新构建）
#   ./scripts/dev-docker.sh rebuild  — 强制重新构建镜像并启动
#   ./scripts/dev-docker.sh status   — 查看容器状态
#   ./scripts/dev-docker.sh logs     — 查看所有服务日志（实时）
#   ./scripts/dev-docker.sh logs frontend|gateway|langgraph|nginx — 查看单个服务日志
#   ./scripts/dev-docker.sh shell gateway|langgraph|frontend — 进入容器 shell

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
DOCKER_DIR="$PROJECT_ROOT/docker"

# 加载 .env 获取 DEV_PORT
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a; source "$PROJECT_ROOT/.env"; set +a
fi

DEV_PORT="${DEV_PORT:-2027}"
COMPOSE_CMD="docker compose -p deer-flow-dev -f $DOCKER_DIR/docker-compose-dev.yaml"

GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

_banner() {
    echo ""
    echo -e "${GREEN}=========================================="
    echo "  DeerFlow 开发环境"
    echo -e "==========================================${NC}"
    echo ""
}

_ready_msg() {
    echo ""
    echo -e "${GREEN}✓ 开发环境已就绪${NC}"
    echo ""
    echo "  访问地址:  http://localhost:${DEV_PORT}"
    echo "  API 文档:  http://localhost:${DEV_PORT}/api/docs"
    echo "  数据目录:  ~/.deer-flow-dev"
    echo ""
    echo "  热加载:"
    echo "    前端 src/  → 修改即生效（Next.js HMR）"
    echo "    后端 app/  → 修改即重启（uvicorn --reload）"
    echo ""
    echo "  日志:  ./scripts/dev-docker.sh logs [service]"
    echo "  停止:  ./scripts/dev-docker.sh stop"
    echo ""
}

_ensure_config_files() {
    # extensions_config.json 必须是文件，否则 Docker bind mount 会创建目录导致启动失败
    if [ ! -f "$PROJECT_ROOT/extensions_config.json" ]; then
        if [ -f "$PROJECT_ROOT/extensions_config.example.json" ]; then
            cp "$PROJECT_ROOT/extensions_config.example.json" "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created extensions_config.json from example${NC}"
        else
            echo "{}" > "$PROJECT_ROOT/extensions_config.json"
            echo -e "${BLUE}Created empty extensions_config.json${NC}"
        fi
    elif [ -d "$PROJECT_ROOT/extensions_config.json" ]; then
        rm -rf "$PROJECT_ROOT/extensions_config.json"
        cp "$PROJECT_ROOT/extensions_config.example.json" "$PROJECT_ROOT/extensions_config.json" 2>/dev/null || echo "{}" > "$PROJECT_ROOT/extensions_config.json"
        echo -e "${YELLOW}Fixed extensions_config.json (was a directory)${NC}"
    fi
}

cmd_start() {
    _banner
    export DEER_FLOW_ROOT="$PROJECT_ROOT"
    _ensure_config_files
    echo -e "${BLUE}构建并启动容器...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD up --build -d --remove-orphans frontend gateway langgraph nginx
    _ready_msg
}

cmd_rebuild() {
    _banner
    export DEER_FLOW_ROOT="$PROJECT_ROOT"
    echo -e "${BLUE}强制重新构建镜像...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD build --no-cache frontend gateway langgraph
    cd "$DOCKER_DIR" && $COMPOSE_CMD up -d --remove-orphans frontend gateway langgraph nginx
    _ready_msg
}

cmd_stop() {
    export DEER_FLOW_ROOT="$PROJECT_ROOT"
    echo -e "${YELLOW}停止开发环境容器...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD down
    echo -e "${GREEN}✓ 已停止${NC}"
}

cmd_restart() {
    export DEER_FLOW_ROOT="$PROJECT_ROOT"
    echo -e "${BLUE}重启容器...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD restart
    echo -e "${GREEN}✓ 已重启${NC}"
    echo "  访问地址: http://localhost:${DEV_PORT}"
}

cmd_status() {
    echo ""
    echo -e "${BLUE}容器状态:${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD ps
    echo ""
    echo -e "${BLUE}端口占用:${NC}"
    ss -tlnp 2>/dev/null | grep -E "2027|2026|8001|2024|3000" || echo "  (无)"
    echo ""
}

cmd_logs() {
    local service="${1:-}"
    export DEER_FLOW_ROOT="$PROJECT_ROOT"
    if [ -n "$service" ]; then
        echo -e "${BLUE}查看 $service 日志...${NC}"
        cd "$DOCKER_DIR" && $COMPOSE_CMD logs -f "$service"
    else
        echo -e "${BLUE}查看所有服务日志...${NC}"
        cd "$DOCKER_DIR" && $COMPOSE_CMD logs -f
    fi
}

cmd_shell() {
    local service="${1:-gateway}"
    echo -e "${BLUE}进入 $service 容器...${NC}"
    cd "$DOCKER_DIR" && $COMPOSE_CMD exec "$service" /bin/bash
}

cmd_help() {
    echo "用法: $0 <命令> [参数]"
    echo ""
    echo "命令:"
    echo "  start              构建并启动开发环境（首次或依赖变更后使用）"
    echo "  stop               停止所有容器"
    echo "  restart            重启容器（不重新构建）"
    echo "  rebuild            强制重新构建镜像并启动"
    echo "  status             查看容器和端口状态"
    echo "  logs [service]     查看日志（实时），service 可选: frontend gateway langgraph nginx"
    echo "  shell [service]    进入容器 shell，默认 gateway"
    echo ""
    echo "访问地址: http://localhost:${DEV_PORT}"
}

case "${1:-help}" in
    start)   cmd_start ;;
    stop)    cmd_stop ;;
    restart) cmd_restart ;;
    rebuild) cmd_rebuild ;;
    status)  cmd_status ;;
    logs)    cmd_logs "${2:-}" ;;
    shell)   cmd_shell "${2:-gateway}" ;;
    help|--help|-h) cmd_help ;;
    *)
        echo "未知命令: $1"
        cmd_help
        exit 1
        ;;
esac
