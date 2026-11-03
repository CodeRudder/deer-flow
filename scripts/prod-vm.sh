#!/usr/bin/env bash
# prod-vm.sh — deer-flow 正式环境（持久容器方案）统一管理入口
#
# 与旧方案的关系：scripts/deploy.sh + docker/docker-compose.prod.yaml 是旧的
# "构建应用镜像"方案（deer-flow-{frontend,gateway}:latest），**保留未删**，仍可
# 使用；本脚本是新的生产路径——与 dev-vm.sh 同构的"标准系统镜像 + 持久容器 +
# bind mount"方案，**零镜像构建**（仅 python:3.12-slim-bookworm / nginx:alpine
# 两个官方基础镜像）。**代码不直接挂载仓库**——deploy 快照（rsync 排除
# .git/依赖/构建产物/数据）复制到部署目录 app/ 后挂载，避免开发工作区的
# 未提交改动、数据文件耦合进生产。前端构建产物放在命名卷。
#
# 容器拓扑（与 dev 的 2027 nginx 共存，生产对外端口 2026）：
#   deer-flow-prod       python:3.12-slim-bookworm  -p 18000:8001（前端 3000 仅容器网络内可见）
#   deer-flow-nginx-prod nginx:alpine               -p 2026:80  → deer-flow-prod:3000/:8001
# 两容器同在 deer-flow-prod-net 网络，nginx 以容器名做 upstream（不占宿主 3000，
# 与 dev 的 3000/18001/2027 端口互不冲突，可同时运行）。
#
# 部署目录（缺省 ~/deer-flow-deploy，可用 DEER_FLOW_DEPLOY_DIR 覆盖）：
#   app/                    仓库代码快照（deploy 命令 rsync 生成，容器挂载点）
#   .env                    进程 env（up 时 source，不进容器 env）
#   config.yaml             DEER_FLOW_CONFIG_PATH
#   extensions_config.json  DEER_FLOW_EXTENSIONS_CONFIG_PATH
#   nginx/nginx.conf        生产 nginx 配置（由 docker/nginx/nginx.conf 适配生成）
#   data/deer-flow/         DEER_FLOW_HOME（全部运行数据）
#
# 用法：prod-vm.sh deploy|init|setup|build-fe|up|stop|restart|status|exec
set -e
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
NAME=deer-flow-prod
NGINX_NAME=deer-flow-nginx-prod
IMAGE=python:3.12-slim-bookworm
DEPLOY_DIR="${DEER_FLOW_DEPLOY_DIR:-$HOME/deer-flow-deploy}"
DEPLOY_APP="$DEPLOY_DIR/app"

# rsync 复制代码快照：只带运行所需源码/配置，排除版本库、依赖、构建产物、
# 运行数据与开发残留。--delete 保证快照与仓库一致（被删文件同步移除）。
RSYNC_EXCLUDES=(
  --exclude .git --exclude .github --exclude .venv --exclude node_modules
  --exclude .next --exclude .turbo --exclude __pycache__ --exclude '*.pyc'
  --exclude .pytest_cache --exclude .ruff_cache --exclude .mypy_cache
  --exclude /docs --exclude /tests --exclude backend/tests
  --exclude /outputs --exclude /data --exclude '.env*' --exclude /backend/.deer-flow
  --exclude /backend/ref*.jpg --exclude docker/dev --exclude scripts/dev-vm.sh
)

deploy_code() {  # 复制仓库代码快照到部署目录（生产只运行这份副本）
  command -v rsync >/dev/null || { echo "需要 rsync"; exit 1; }
  mkdir -p "$DEPLOY_APP"
  # 先删标记文件避免 --delete 误删命名卷（卷不在快照树内，安全），再整体同步
  rsync -a --delete "${RSYNC_EXCLUDES[@]}" "$REPO_ROOT"/ "$DEPLOY_APP"/
  echo "代码快照已复制：$DEPLOY_APP（来源 $REPO_ROOT）"
}

stage_nginx_conf() {  # 由仓库生产 nginx 配置适配：监听 80，upstream 指向宿主映射端口
  mkdir -p "$DEPLOY_DIR/nginx"
  sed -e 's/listen 2026 default_server;/listen 80 default_server;/' \
      -e 's/listen \[::\]:2026 default_server;/listen [::]:80 default_server;/' \
      -e 's/set $gateway_upstream gateway:8001;/set $gateway_upstream deer-flow-prod:8001;/' \
      -e 's/set $frontend_upstream frontend:3000;/set $frontend_upstream deer-flow-prod:3000;/' \
      "$REPO_ROOT/docker/nginx/nginx.conf" > "$DEPLOY_DIR/nginx/nginx.conf"
}

gen_secrets() {  # 秘钥持久化在部署目录 data/ 下，重跑 setup 不变
  local _file _val
  _file="$DEPLOY_DIR/data/.better-auth-secret"
  if [ ! -s "$_file" ]; then
    _val="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    printf '%s' "$_val" > "$_file"; chmod 600 "$_file"
  fi
  _file="$DEPLOY_DIR/data/.internal-auth-token"
  if [ ! -s "$_file" ]; then
    _val="$(openssl rand -hex 32 2>/dev/null || head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
    printf '%s' "$_val" > "$_file"; chmod 600 "$_file"
  fi
}

stage_deploy_dir() {  # 每次刷新静态配置；data/ 与 .env 中已生成的秘钥不动
  mkdir -p "$DEPLOY_DIR/data/deer-flow"
  gen_secrets
  if [ ! -f "$DEPLOY_DIR/config.yaml" ]; then
    if [ -f "$REPO_ROOT/config.yaml" ]; then cp "$REPO_ROOT/config.yaml" "$DEPLOY_DIR/config.yaml"
    elif [ -f "$REPO_ROOT/config.example.yaml" ]; then cp "$REPO_ROOT/config.example.yaml" "$DEPLOY_DIR/config.yaml"
    else echo "警告：仓库无 config.yaml / config.example.yaml，需自行提供 $DEPLOY_DIR/config.yaml"; fi
  fi
  if [ ! -f "$DEPLOY_DIR/extensions_config.json" ]; then
    if [ -f "$REPO_ROOT/extensions_config.json" ]; then cp "$REPO_ROOT/extensions_config.json" "$DEPLOY_DIR/extensions_config.json"
    else echo '{"mcpServers":{},"skills":{}}' > "$DEPLOY_DIR/extensions_config.json"; fi
  fi
  # .env：仓库 .env 为底 + 前端/生产键追加（重复键后者生效）；秘钥从 data/ 读入
  local host_ip; host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"; host_ip="${host_ip:-127.0.0.1}"
  { [ -f "$REPO_ROOT/.env" ] && cat "$REPO_ROOT/.env" || true
    [ -f "$REPO_ROOT/frontend/.env" ] && cat "$REPO_ROOT/frontend/.env" || true
    echo "BETTER_AUTH_SECRET=$(cat "$DEPLOY_DIR/data/.better-auth-secret")"
    echo "BETTER_AUTH_URL=${BETTER_AUTH_URL:-http://$host_ip:2026}"
    echo "DEER_FLOW_INTERNAL_AUTH_TOKEN=$(cat "$DEPLOY_DIR/data/.internal-auth-token")"
    # 生产数据库连接串覆盖（data/.database-url 持久化，键后置生效）——避免仓库 .env
    # 里面向 dev 的 DATABASE_URL 在重跑 setup 时覆盖生产值。
    if [ -s "$DEPLOY_DIR/data/.database-url" ]; then
      echo "DATABASE_URL=$(cat "$DEPLOY_DIR/data/.database-url")"
    fi
  } > "$DEPLOY_DIR/.env"
  stage_nginx_conf
}

init() {  # 一次性创建两个持久容器（幂等：已存在则跳过）
  docker network inspect deer-flow-prod-net >/dev/null 2>&1 || docker network create deer-flow-prod-net >/dev/null
  stage_nginx_conf
  if ! docker inspect "$NAME" >/dev/null 2>&1; then
    docker run -d --name "$NAME" --restart unless-stopped \
      --network deer-flow-prod-net \
      -p 18000:8001 \
      -v "$DEPLOY_APP":/app \
      -v "$DEPLOY_DIR":/deploy \
      -v deer-flow-prod-tools:/opt/tools \
      -v deer-flow-prod-venv:/app/backend/.venv \
      -v deer-flow-prod-node_modules:/app/frontend/node_modules \
      -v deer-flow-prod-next:/app/frontend/.next \
      -w /app "$IMAGE" sleep infinity >/dev/null
    echo "容器 $NAME 已创建。"
  else
    echo "容器 $NAME 已存在（跳过）。"
  fi
  if ! docker inspect "$NGINX_NAME" >/dev/null 2>&1; then
    docker run -d --name "$NGINX_NAME" --restart unless-stopped \
      --network deer-flow-prod-net \
      -p 2026:80 \
      -v "$DEPLOY_DIR/nginx/nginx.conf":/etc/nginx/nginx.conf:ro \
      nginx:alpine sh -c 'test -e /proc/net/if_inet6 || sed -i "/listen \[::\]:80/d" /etc/nginx/nginx.conf; exec nginx -g "daemon off;"' >/dev/null
    echo "容器 $NGINX_NAME 已创建。"
  else
    echo "容器 $NGINX_NAME 已存在（跳过）。"
  fi
  echo "下一步：prod-vm.sh deploy && prod-vm.sh setup（首次或代码变更后）"
}

setup() {  # 一次性：apt 运行依赖 + 工具链 + uv sync + 前端生产构建 + 部署目录文件
  stage_deploy_dir
  # 数据库 backend=postgres 时带上 postgres extra（asyncpg / psycopg /
  # langgraph-checkpoint-postgres），与根 Makefile / serve.sh 的自动探测一致
  local UV_EXTRA=""
  if grep -Eq '^database:' "$DEPLOY_DIR/config.yaml" && \
     sed -n '/^database:/,/^[^ ]/p' "$DEPLOY_DIR/config.yaml" | grep -q 'backend: postgres'; then
    UV_EXTRA="--extra postgres"
  fi
  docker exec "$NAME" bash -c '
    set -e
    sed -i "s|deb.debian.org|mirrors.tuna.tsinghua.edu.cn|g" /etc/apt/sources.list.d/debian.sources /etc/apt/sources.list 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq curl ffmpeg xz-utils ca-certificates procps >/dev/null
    export TOOLS=/opt/tools; mkdir -p $TOOLS/node $TOOLS/pnpm $TOOLS/uv-bin
    [ -x $TOOLS/node/bin/node ] || { curl -fsSL https://nodejs.org/dist/v22.14.0/node-v22.14.0-linux-arm64.tar.xz | tar -xJ -C $TOOLS --strip-components=1 --one-top-level=node; }
    export PATH=$TOOLS/node/bin:$PATH
    npm install -g pnpm@9 --prefix $TOOLS/pnpm >/dev/null 2>&1
    [ -x $TOOLS/uv-bin/uv ] || curl -fsSL https://astral.sh/uv/install.sh | UV_INSTALL_DIR=$TOOLS/uv-bin sh >/dev/null
    cd /app/backend && $TOOLS/uv-bin/uv sync --no-dev '"$UV_EXTRA"'
    cd /app/frontend && PATH=$TOOLS/pnpm/bin:$PATH pnpm install
    PATH=$TOOLS/pnpm/bin:$PATH pnpm build
    grep -q /opt/tools /root/.bashrc 2>/dev/null || cat >> /root/.bashrc << EOF2
export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:\$PATH
EOF2
    echo SETUP-OK'
  docker exec "$NGINX_NAME" nginx -s reload 2>/dev/null || docker start "$NGINX_NAME" >/dev/null
  echo "setup 完成。启动：prod-vm.sh up"
}

build_fe() {  # 仅重跑前端生产构建（代码变更后）
  docker exec "$NAME" bash -c '
    set -e; export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH
    cd /app/frontend && pnpm install && pnpm build && echo BUILD-OK'
}

up() {  # 启动 deerflow 进程（env 从 /deploy/.env 注入进程，不进容器 env），并确保 nginx 在跑
  docker start "$NGINX_NAME" >/dev/null 2>&1 || true
  docker exec -d "$NAME" bash -c 'set -a; . /deploy/.env; set +a
    export PATH=/opt/tools/node/bin:/opt/tools/pnpm/bin:/opt/tools/uv-bin:$PATH
    export NODE_ENV=production CI=true
    export DEER_FLOW_PROJECT_ROOT=/app
    export DEER_FLOW_CONFIG_PATH=/deploy/config.yaml
    export DEER_FLOW_HOME=/deploy/data/deer-flow
    export DEER_FLOW_EXTENSIONS_CONFIG_PATH=/deploy/extensions_config.json
    setsid bash -c "cd /app/backend && PYTHONPATH=.:packages/harness uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --workers ${GATEWAY_WORKERS:-2} > /tmp/backend.log 2>&1" < /dev/null &
    setsid bash -c "cd /app/frontend && exec env PORT=3000 HOSTNAME=0.0.0.0 node server.gzip.mjs > /tmp/frontend.log 2>&1" < /dev/null &
    exit 0"'
  wait_healthy
}

# 轮询三个端点直至全通或超时（~60s）；失败时 tail 日志尾部辅助定位
wait_healthy() {
  local i ok
  printf '等待服务就绪'
  for i in $(seq 1 60); do
    ok=1
    curl -s -o /dev/null --max-time 3 http://localhost:2026 || ok=0
    docker exec "$NGINX_NAME" curl -s -o /dev/null --max-time 3 http://"$NAME":3000 2>/dev/null || ok=0
    curl -s -o /dev/null --max-time 3 http://localhost:18000/health || ok=0
    [ "$ok" = 1 ] && { echo " 就绪（${i}0s 内）"; status; return 0; }
    printf '.'; sleep 1
  done
  echo
  echo "错误：服务在 60s 内未全部就绪，最近日志："
  docker exec "$NAME" bash -c 'tail -n 30 /tmp/backend.log /tmp/frontend.log 2>/dev/null' || true
  return 1
}

stop() {
  # pkill 后轮询 pgrep 确认进程真正退出（避免 stop→up 时序竞争），超时 kill -9 兜底
  docker exec "$NAME" bash -c '
    pkill -f "[u]vicorn app.gateway" 2>/dev/null || true
    pkill -f "[s]erver.gzip" 2>/dev/null || true
    for i in $(seq 1 20); do
      pgrep -f "[u]vicorn app.gateway|[s]erver.gzip" >/dev/null 2>&1 || exit 0
      sleep 0.5
    done
    pkill -9 -f "[u]vicorn app.gateway|[s]erver.gzip" 2>/dev/null || true
    sleep 1'
  echo 进程已停
}

status() {
  curl -s -o /dev/null -w 'nginx    :2026  → %{http_code}\n' --max-time 3 http://localhost:2026 || echo 'nginx    未响应'
  # 前端 next server 绑定在容器主机名接口（非 localhost），从 nginx 容器经 docker 网络探测
  docker exec "$NGINX_NAME" curl -s -o /dev/null -w 'frontend :3000  → %{http_code}（经 nginx 容器）\n' --max-time 3 http://"$NAME":3000 2>/dev/null || echo 'frontend 未响应'
  curl -s -o /dev/null -w 'gateway  :18000 → %{http_code}\n' --max-time 3 http://localhost:18000/health || echo 'gateway  未响应'
  echo "logs: docker exec $NAME tail -f /tmp/backend.log /tmp/frontend.log"
}

case "${1:-}" in
  deploy) deploy_code ;;
  init) init ;; setup) setup ;; build-fe) build_fe ;; up) up ;; stop) stop ;;
  restart) stop; up ;;
  status) status ;;
  exec) docker exec -it "$NAME" bash ;;
  *) echo "用法: $0 init|setup|build-fe|up|stop|restart|status|exec"; exit 1 ;;
esac
