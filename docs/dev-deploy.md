# DeerFlow 开发环境部署指南

本机已有一套生产实例（`deer-flow/`，端口 2026），开发环境（`deer-flow-dev/`）通过 Docker 与其完全隔离。

## 隔离策略

| 维度 | 生产实例 | 开发实例 |
|------|----------|----------|
| 项目路径 | `~/work/projects/deer-flow/` | `~/work/projects/deer-flow-dev/` |
| 运行方式 | 本地进程（serve.sh） | Docker 容器 |
| 外部访问端口 | 2026 | **2027** |
| 数据目录 | `~/.deer-flow` | `~/.deer-flow-dev` |
| Docker project | — | `deer-flow-dev` |
| 容器名前缀 | — | `deer-flow-{service}` |

## 热加载支持

| 服务 | 热加载方式 | 挂载路径 |
|------|-----------|----------|
| 前端 (Next.js) | HMR（`WATCHPACK_POLLING=true`） | `frontend/src/`, `frontend/public/` |
| Gateway (uvicorn) | `--reload` 自动重启 | `backend/` 整目录 |
| LangGraph | `langgraph dev` 自动重启 | `backend/` 整目录 |

修改代码后无需重启容器，直接生效。  
只有修改 `pyproject.toml`（新增依赖）时才需要 `rebuild`。

## 快速开始

```bash
cd ~/work/projects/deer-flow-dev

# 首次启动（构建镜像，约 5-10 分钟）
./scripts/dev-docker.sh start

# 访问
open http://localhost:2027
```

## 常用命令

```bash
# 启动（首次或依赖变更后）
./scripts/dev-docker.sh start

# 查看状态
./scripts/dev-docker.sh status

# 查看所有日志
./scripts/dev-docker.sh logs

# 查看单个服务日志
./scripts/dev-docker.sh logs gateway
./scripts/dev-docker.sh logs langgraph
./scripts/dev-docker.sh logs frontend

# 停止
./scripts/dev-docker.sh stop

# 重启（不重新构建）
./scripts/dev-docker.sh restart

# 强制重新构建（修改了 pyproject.toml 或 Dockerfile 后）
./scripts/dev-docker.sh rebuild

# 进入容器调试
./scripts/dev-docker.sh shell gateway
./scripts/dev-docker.sh shell langgraph
```

也可以直接用 Makefile：

```bash
make docker-start   # 等同于 dev-docker.sh start
make docker-stop    # 等同于 dev-docker.sh stop
make docker-logs    # 查看日志
```

## 端口布局

```
宿主机:2027  →  nginx容器:2026
                  ├── /api/langgraph/*  →  langgraph:2024
                  ├── /api/*            →  gateway:8001
                  └── /*                →  frontend:3000
```

内部端口（2024/8001/3000）不暴露到宿主机，不与生产实例冲突。

## 数据文件位置

```
~/.deer-flow-dev/
├── checkpoints.db        # LangGraph 状态存储
├── memory.json           # 全局记忆
├── threads/              # 会话数据
│   └── {thread_id}/
│       ├── *.jsonl       # 消息历史
│       └── user-data/    # 上传文件
└── agents/               # 自定义 Agent 配置
```

## 配置文件

- `config.yaml` — 模型、sandbox、日志等配置，修改后 gateway/langgraph 自动重载
- `.env` — API Keys、端口、数据目录路径
- `extensions_config.json` — 扩展配置

## 注意事项

- 首次 `start` 需要构建 Docker 镜像，耗时较长
- 后续启动直接复用已构建的镜像，速度很快
- `.venv` 保存在 Docker named volume 中，不会因代码挂载被覆盖
- 如遇容器启动失败，先查日志：`./scripts/dev-docker.sh logs gateway`
- `extensions_config.json` 必须是文件而非目录，`dev-docker.sh start` 会自动检查并修复；若手动用 `make docker-start` 启动，需确保该文件已存在（可从 `extensions_config.example.json` 复制）
- nginx 重建后需要重启（`./scripts/dev-docker.sh restart`）以重新解析容器 IP
