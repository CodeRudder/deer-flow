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

port_pids() {  # 容器内：按端口找占用进程 pid（经 /proc/net/tcp inode → /proc/*/fd，不依赖 ss/netstat）
  docker exec "$NAME" bash -c '
    port=$1
    hexport=$(printf "%04X" "$port")
    inodes=$(awk -v hex="$hexport" "\$4 == \"0A\" && substr(\$2, length(\$2)-3) == hex {print \$10}" /proc/net/tcp /proc/net/tcp6 2>/dev/null)
    [ -z "$inodes" ] && exit 0
    for pid in $(ls /proc | grep -E "^[0-9]+$"); do
      for ino in $inodes; do
        if ls -l /proc/$pid/fd 2>/dev/null | grep -q "socket:\[$ino\]"; then
          echo "$pid"
          break
        fi
      done
    done' _ "$1"
}

precheck_ports() {  # 启动前预检 8001/3000：被占用则清理，清不掉报错退出（避免残留进程导致 Address already in use）
  local port pids label
  for port in 8001 3000; do
    [ "$port" = 8001 ] && label=backend || label=frontend
    pids=$(port_pids "$port")
    [ -z "$pids" ] && continue
    echo "端口 $port 被 pid($pids) 占用，尝试清理…"
    docker exec "$NAME" bash -c 'kill '"$pids"' 2>/dev/null || true'
    sleep 2
    pids=$(port_pids "$port")
    if [ -n "$pids" ]; then
      docker exec "$NAME" bash -c 'kill -9 '"$pids"' 2>/dev/null || true'
      sleep 1
      pids=$(port_pids "$port")
    fi
    if [ -n "$pids" ]; then
      echo "错误：端口 $port 仍被 pid($pids) 占用，无法启动 $label。请进容器排查：dev-vm.sh exec"
      exit 1
    fi
    echo "端口 $port 已清理"
  done
}

http_code() { curl -s -o /dev/null -w '%{http_code}' --max-time 3 "$1" 2>/dev/null || true; }

wait_healthy() {  # 轮询 gateway(:18001)/frontend(:3000) 直至就绪（~60s）；失败 tail 日志诊断
  local i gw fe
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
  echo "错误：服务在 60s 内未全部就绪，最近日志："
  docker exec "$NAME" bash -c 'tail -n 30 /tmp/backend.log /tmp/frontend.log 2>/dev/null' || true
  return 1
}

up() {  # 启动 deerflow 进程（env 从 .env 注入进程，不进容器）
  precheck_ports
  docker exec -d "$NAME" bash -c 'set -a; . /app/.env; set +a
    export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH
    setsid bash -c "cd /app/backend && PYTHONPATH=.:packages/harness uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload > /tmp/backend.log 2>&1" < /dev/null &
    setsid bash -c "cd /app/frontend && pnpm dev --port 3000 --hostname 0.0.0.0 > /tmp/frontend.log 2>&1" < /dev/null &
    exit 0'
  wait_healthy
}

stop() {
  # 1) pkill 后轮询 pgrep 确认退出；2) 按端口(8001/3000)兜底清理残留占用
  #    （uvicorn --reload 的 worker 进程名是 "python -c ... spawn_main"，pkill 模式
  #    匹配不到，孤儿 worker 会占住端口导致下次启动 Address already in use）
  docker exec "$NAME" bash -c '
    port_pids() {
      local port=$1 hex inodes ino pid
      hex=$(printf "%04X" "$port")
      inodes=$(awk -v hex="$hex" "\$4 == \"0A\" && substr(\$2, length(\$2)-3) == hex {print \$10}" /proc/net/tcp /proc/net/tcp6 2>/dev/null)
      [ -z "$inodes" ] && return 0
      for pid in $(ls /proc | grep -E "^[0-9]+$"); do
        for ino in $inodes; do
          if ls -l /proc/$pid/fd 2>/dev/null | grep -q "socket:\[$ino\]"; then
            echo "$pid"; break
          fi
        done
      done
    }
    kill_patterns() {
      pkill -f "[u]vicorn app.gateway" 2>/dev/null || true
      pkill -f "[n]ext dev" 2>/dev/null || true
      pkill -f "[n]ext-server" 2>/dev/null || true
      pkill -f "[s]pawn_main" 2>/dev/null || true
    }
    kill_patterns
    for i in $(seq 1 20); do
      pgrep -f "[u]vicorn app.gateway|[n]ext|[s]pawn_main" >/dev/null 2>&1 || break
      sleep 0.5
    done
    pkill -9 -f "[u]vicorn app.gateway|[n]ext|[s]pawn_main" 2>/dev/null || true
    # 端口兜底：确保 8001/3000 真正释放，避免 stop→up 时序竞争
    for port in 8001 3000; do
      for i in $(seq 1 10); do
        pids=$(port_pids "$port")
        [ -z "$pids" ] && break
        kill $pids 2>/dev/null; sleep 1
        pids=$(port_pids "$port")
        [ -n "$pids" ] && kill -9 $pids 2>/dev/null
        sleep 0.3
      done
    done'
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
