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

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || true; }

wait_healthy() {  # up 后从宿主侧轮询映射端口直至就绪（~60s）；失败输出占用进程与日志诊断
  local i gw fe port pid
  printf '等待服务就绪'
  for i in $(seq 1 60); do
    gw=$(http_code http://localhost:18001/health)
    fe=$(http_code http://localhost:3000)
    # 仅接受真实服务状态码（404/000 可能是端口被无关进程占用的假信号）
    if { [ "$gw" = 200 ] || [ "$gw" = 307 ]; } && { [ "$fe" = 200 ] || [ "$fe" = 307 ]; }; then
      echo " 就绪（${i}s 内）"; status; return 0
    fi
    printf '.'; sleep 1
  done
  echo
  echo "错误：启动失败 — 服务在 60s 内未全部就绪（gateway=$gw frontend=$fe）。"
  for port in 18001 3000; do
    if ss -ltn "sport = :$port" 2>/dev/null | grep -q LISTEN; then
      pid=$(ss -ltnp "sport = :$port" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u | tr '\n' ' ')
      if [ -n "$pid" ]; then
        echo "宿主端口 $port 被占用，pid: $pid ($(ps -o comm= -p "${pid%% *}" 2>/dev/null)) — 宿主进程不自动杀，请手动处理"
      else
        echo "宿主端口 $port 被占用（pid 不可见，需 root：sudo ss -ltnp sport = :$port）"
      fi
    else
      echo "宿主端口 $port 无监听（容器内服务未就绪，见下方日志）"
    fi
  done
  echo "容器内最近日志："
  docker exec "$NAME" bash -c 'tail -n 30 /tmp/backend.log /tmp/frontend.log 2>/dev/null' || true
  return 1
}

up() {  # 启动 deerflow 进程（env 从 .env 注入进程，不进容器）
  docker exec -d "$NAME" bash -c 'set -a; . /app/.env; set +a
    export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH
    # watch 监听必须圈定在源码目录（app/packages）：--reload-exclude 的 glob
    # 匹配不了 watchfiles 上报的嵌套相对路径（沙箱 .venv 写文件曾反复触发
    # reload 风暴→SSE 卡死→run 孤儿化，见 47a3774b 排查）。config.yaml 热载
    # 走 AppConfig 内容签名机制，不依赖 watcher。
    # （注意本行位于外层 docker exec 的单引号串内，路径无需再加引号）
    setsid bash -c "cd /app/backend && PYTHONPATH=.:packages/harness uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload-dir app --reload-dir packages > /tmp/backend.log 2>&1" < /dev/null &
    setsid bash -c "cd /app/frontend && pnpm dev --port 3000 --hostname 0.0.0.0 > /tmp/frontend.log 2>&1" < /dev/null &
    exit 0'
  wait_healthy
}

stop() {
  # pkill 覆盖完整启动链（uv run 包装 / uvicorn / --reload worker(spawn_main) / next），
  # 模式加 [] 防自匹配；轮询确认退出（10s），超时 kill -9 兜底
  docker exec "$NAME" bash -c '
    # 注意：容器内脚本不得出现明文进程关键字（本 bash 的 cmdline 会被外部
    # pkill -f 扫到误杀），一律用 [x] 括号形式
    pat="[u]vicorn|[s]pawn_main|[n]ext dev|[n]ext-server"
    pkill -f "[u]vicorn app.gateway" 2>/dev/null || true
    pkill -f "[s]pawn_main"          2>/dev/null || true
    pkill -f "[n]ext dev"            2>/dev/null || true
    pkill -f "[n]ext-server"         2>/dev/null || true
    for i in $(seq 1 20); do
      live=0
      for p in $(pgrep -f "$pat"); do
        # 跳过僵尸（容器 PID1 不回收，僵尸 cmdline 为空但 comm 仍会被 pgrep 匹配）
        if [ "$(awk "{print \$3}" /proc/$p/stat 2>/dev/null)" != Z ]; then live=1; fi
      done
      if [ "$live" = 0 ]; then exit 0; fi
      sleep 0.5
    done
    pkill -9 -f "$pat" 2>/dev/null || true
    sleep 1'
  echo 进程已停
}

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
