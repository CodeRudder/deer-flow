#!/usr/bin/env bash
# dev-vm.sh — deer-flow 开发虚拟机（单容器）统一管理入口
# 用法：dev-vm.sh init|up|stop|restart|status|exec
# 容器创建参数固定于此；deerflow 的 env（.env）在进程启动时注入，不放容器环境变量。
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=deer-flow-dev
IMAGE=python:3.12-slim-bookworm

init() {  # 一次性创建容器（幂等：已存在则跳过）
  docker inspect "$NAME" >/dev/null 2>&1 && { echo "容器 $NAME 已存在（stop/start 即可）"; return 0; }
  docker run -d --name "$NAME" --restart unless-stopped \
    -p 3000:3000 -p 18001:8001 -p 2222:22 \
    -v "$REPO_ROOT":/app \
    -v "$HOME/.ssh/authorized_keys":/tmp/ak:ro \
    -v deer-flow-dev-tools:/opt/tools \
    -v deer-flow-dev-venv:/app/backend/.venv \
    -v deer-flow-dev-node_modules:/app/frontend/node_modules \
    -w /app "$IMAGE" bash /app/docker/dev/entrypoint.sh >/dev/null
  echo "容器已创建。首次或依赖变更后执行：dev-vm.sh setup"
}

setup() {  # 一次性：中间件(apt, 国内源)+工具链(node/pnpm/uv→命名卷)+依赖(.venv/node_modules)
  docker exec "$NAME" bash -c '
    sed -i "s|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq openssh-server openssh-client curl ffmpeg xz-utils >/dev/null
    ssh-keygen -A >/dev/null; mkdir -p /run/sshd; /usr/sbin/sshd
    export TOOLS=/opt/tools; mkdir -p $TOOLS/node $TOOLS/pnpm $TOOLS/uv-bin
    [ -x $TOOLS/node/bin/node ] || { curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-arm64.tar.xz | tar -xJ -C $TOOLS --strip-components=1 --one-top-level=node; }
    export PATH=$TOOLS/node/bin:$PATH
    npm install -g pnpm@9 --prefix $TOOLS/pnpm >/dev/null 2>&1
    [ -x $TOOLS/uv-bin/uv ] || curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR=$TOOLS/uv-bin sh >/dev/null
    cd /app/backend && $TOOLS/uv-bin/uv sync
    cd /app/frontend && PATH=$TOOLS/pnpm/bin:$PATH pnpm install
    grep -q /opt/tools /root/.bashrc 2>/dev/null || cat >> /root/.bashrc << EOF2
export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:\$PATH
EOF2
    echo SETUP-OK'
}

up() {  # 启动 deerflow 进程（env 从 .env 注入进程，不进容器）
  docker exec -d "$NAME" bash -c 'set -a; . /app/.env; set +a
    export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH
    cd /app/backend && PYTHONPATH=.:packages/harness nohup uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload > /tmp/backend.log 2>&1 &
    cd /app/frontend && nohup pnpm dev --port 3000 --hostname 0.0.0.0 > /tmp/frontend.log 2>&1 &'
  sleep 10
  status
}

stop() { docker exec "$NAME" bash -c 'pkill -f "uvicorn app.gateway" || true; pkill -f "next dev" || true'; echo 进程已停; }

status() {
  curl -s -o /dev/null -w 'gateway  :18001 → %{http_code}\n' --max-time 3 http://localhost:18001/health || echo 'gateway  未响应'
  curl -s -o /dev/null -w 'frontend :3000  → %{http_code}\n' --max-time 3 http://localhost:3000 || echo 'frontend 未响应'
  echo "ssh: ssh -p 2222 root@localhost"
}

case "${1:-}" in
  init) init ;; setup) setup ;; up) up ;; stop) stop ;;
  restart) stop; up ;;
  status) status ;;
  exec) docker exec -it "$NAME" bash ;;
  *) echo "用法: $0 init|setup|up|stop|restart|status|exec"; exit 1 ;;
esac
