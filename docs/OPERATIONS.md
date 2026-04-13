# DeerFlow 运维手册

## 目录

- [服务管理](#服务管理)
- [系统服务（systemd）](#系统服务systemd)
- [Docker 部署](#docker-部署)
- [服务状态检查](#服务状态检查)
- [日志管理](#日志管理)
- [运行中会话管理](#运行中会话管理)
- [配置变更](#配置变更)
- [常见问题排查](#常见问题排查)

## 服务管理

### 快捷脚本

| 脚本 | 用途 |
|------|------|
| `./scripts/start.sh [mode]` | 启动服务 |
| `./scripts/stop.sh` | 停止服务 |
| `./scripts/restart.sh [mode]` | 重启服务 |
| `./scripts/status.sh` | 查看各服务状态 |

### 模式说明

| 模式 | 进程数 | 说明 |
|------|--------|------|
| `dev` | 4 | 开发模式，热重载（LangGraph + Gateway + Frontend + Nginx） |
| `prod` | 4 | 生产模式，预编译前端，无热重载 |
| `gateway` | 3 | Gateway 模式（实验性），Agent 运行时嵌入 Gateway，无 LangGraph Server |
| `prod-gateway` | 3 | 生产 + Gateway 模式 |

### 端口分配

| 服务 | 端口 | 说明 |
|------|------|------|
| Nginx | 2026 | 统一入口（反向代理） |
| LangGraph | 2024 | Agent 运行时 |
| Gateway | 8001 | REST API |
| Frontend | 2025 | Next.js 开发服务器 |

### 使用示例

```bash
# 开发模式启动
./scripts/start.sh dev

# 生产模式启动
./scripts/start.sh prod

# Gateway 模式启动
./scripts/start.sh gateway

# 重启（保持当前模式）
./scripts/restart.sh dev

# 停止所有服务
./scripts/stop.sh

# 检查服务状态
./scripts/status.sh
```

### Makefile 快捷命令

```bash
make dev              # 等同 ./scripts/start.sh dev
make dev-pro          # 等同 ./scripts/start.sh gateway
make start            # 等同 ./scripts/start.sh prod
make start-pro        # 等同 ./scripts/start.sh prod-gateway
make stop             # 等同 ./scripts/stop.sh
```

## 系统服务（systemd）

### 安装为系统服务

```bash
# 生产模式（推荐）
sudo ./scripts/install-service.sh prod

# 开发模式
sudo ./scripts/install-service.sh dev

# 指定运行用户
sudo ./scripts/install-service.sh prod username

# Gateway 模式
sudo ./scripts/install-service.sh prod-gateway
```

### 服务管理命令

```bash
sudo systemctl start deerflow       # 启动
sudo systemctl stop deerflow        # 停止
sudo systemctl restart deerflow     # 重启
sudo systemctl status deerflow      # 查看状态
sudo systemctl enable deerflow      # 开机自启
sudo systemctl disable deerflow     # 取消开机自启
```

### 查看日志

```bash
# 实时日志
journalctl -u deerflow -f

# 最近 100 行
journalctl -u deerflow -n 100

# 某个时间段
journalctl -u deerflow --since "2026-04-13 10:00" --until "2026-04-13 12:00"
```

### 卸载系统服务

```bash
sudo ./scripts/uninstall-service.sh
```

## Docker 部署

```bash
# 构建并启动（生产环境）
make up

# Gateway 模式
make up-pro

# 停止
make down
```

Docker 模式下所有服务运行在容器中，统一暴露在 2026 端口。

## 服务状态检查

### 脚本检查

```bash
./scripts/status.sh
```

输出示例：
```
DeerFlow Service Status
=======================
  ● LangGraph (port 2024)
    ✓ HTTP OK
  ● Gateway (port 8001)
    ✓ HTTP OK
  ● Frontend (port 2025)
  ● Nginx (port 2026)
    ✓ HTTP OK

Overall: All services running
  URL: http://localhost:2026
```

### 手动检查

```bash
# 检查 Gateway 健康
curl http://localhost:8001/health

# 检查会话状态
curl http://localhost:8001/api/threads/{thread_id}/status

# 检查子任务列表
curl http://localhost:8001/api/threads/{thread_id}/subagents
```

## 日志管理

### 日志文件位置

```
logs/
├── langgraph.log    # LangGraph Server 日志
├── gateway.log      # Gateway API 日志
├── frontend.log     # Next.js 日志
└── nginx.log        # Nginx 日志
```

### 查看日志

```bash
# 实时跟踪所有日志
tail -f logs/*.log

# 只看 Gateway 日志
tail -f logs/gateway.log

# 搜索错误
grep -i error logs/gateway.log | tail -20

# 搜索特定线程
grep "df886916" logs/gateway.log | tail -20
```

### 日志轮转

日志文件会持续增长。可配置 logrotate：

```bash
sudo tee /etc/logrotate.d/deerflow << 'EOF'
/path/to/deer-flow/logs/*.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    copytruncate
}
EOF
```

将 `/path/to/deer-flow` 替换为实际路径。

## 运行中会话管理

### 查看活跃 Run

```bash
./scripts/stop-all-runs.sh --list
```

### 取消所有活跃 Run

```bash
./scripts/stop-all-runs.sh
```

### 取消单个子任务

```bash
curl -X POST http://localhost:8001/api/runs/subtasks/{task_id}/cancel
```

### 恢复中断的子任务

```bash
curl -X POST http://localhost:8001/api/threads/{thread_id}/subagents/{task_id}/resume
```

### 向运行中的子任务发送消息

```bash
curl -X POST http://localhost:8001/api/runs/subtasks/{task_id}/message \
  -H "Content-Type: application/json" \
  -d '{"message": "请优先处理认证模块"}'
```

## 配置变更

### 主配置文件

```
config.yaml          # 主配置（模型、工具、沙箱、子任务超时等）
extensions_config.json  # MCP 和技能配置
```

### 热重载

以下配置变更无需重启：

- `config.yaml` — Gateway 自动检测 mtime 变化并重载
- `extensions_config.json` — MCP 工具缓存自动失效

### 需要重启的变更

- 模型配置变更（模型类、API Key）
- LangGraph 相关配置
- 端口变更
- Nginx 配置变更

### 配置升级

当 `config.example.yaml` 更新后，合并新字段到现有配置：

```bash
make config-upgrade
```

## 常见问题排查

### 服务启动失败

```bash
# 检查端口占用
lsof -i :2024 -i :8001 -i :2025 -i :2026

# 释放端口
./scripts/stop.sh

# 查看错误日志
tail -30 logs/gateway.log
tail -30 logs/langgraph.log
```

### 子任务全部超时

1. 检查 LLM 服务是否正常：`curl http://localhost:4000/v1/models`
2. 检查子任务超时配置：`config.yaml` → `subagents.timeout_seconds`
3. 查看具体子任务状态：`curl localhost:8001/api/threads/{thread_id}/status`

### 健康监控未激活会话

1. 检查 Gateway 日志中 `Session health monitor` 相关信息
2. 确认 LangGraph Server 可达
3. 检查是否有僵尸 Run 阻塞（`_has_active_run` 返回 True）
4. 健康监控每 3 分钟检查一次，最多激活 5 次

### 前端无法连接后端

1. Nginx 是否运行：`curl http://localhost:2026/`
2. Gateway 是否可达：`curl http://localhost:8001/health`
3. 检查 `frontend/.env.local` 中的 URL 配置
4. 检查 Nginx 配置：`docker/nginx/nginx.local.conf`

### 内存不足

子任务并发数默认为 3（`MAX_CONCURRENT_SUBAGENTS`），每个子任务消耗一个线程池 worker。
如果 LLM 响应很大，可能导致内存压力。

```yaml
# config.yaml 降低并发
subagents:
  timeout_seconds: 900
  # max_turns: 120
```
