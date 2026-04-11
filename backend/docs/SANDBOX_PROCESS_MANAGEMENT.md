# Bash 命令异步执行与进程管理

## Context

主会话中 AI 调用 `bash` 启动 Vite dev server 时，`subprocess.run()` 永久阻塞，导致 Run 卡住 12 分钟。已实现超时机制（120s 杀进程树），但根本问题是：常驻进程（dev server、watcher）无法在同步模式下运行。

**目标**：支持异步方式执行 bash 命令，不阻塞会话。AI 可以启动后台命令、查看输出、列出运行中的命令、终止命令。进程状态持久化到本地，服务重启后可恢复。

---

## 架构总览

```
AI agent
  ├── bash_tool          — 同步执行，适合快速命令（ls, cat, npm run build）
  ├── bash_background    — 异步启动后台命令，立即返回 command_id
  ├── bash_output        — 查看后台命令的输出
  ├── bash_kill          — 终止后台命令
  └── bash_list          — 列出所有后台命令及其状态
          │
          ▼
  ProcessManager (sandbox/process_manager.py)
      ├── 全局注册表 _commands: dict[str, CommandInfo]
      ├── 后台读取线程 per command（持续读 stdout/stderr 到 buffer）
      ├── 状态持久化 → JSON 文件（每次状态变更写入）
      ├── 启动时加载 → 恢复元数据 + 清理孤儿进程
      └── 线程安全（threading.Lock）
          │
          ▼
  subprocess.Popen + os.setsid (进程组)
```

## 1. ProcessManager — 进程管理器

**文件**: `deerflow/sandbox/process_manager.py`

### 数据结构

```python
class CommandStatus(str, Enum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    KILLED = "killed"
    TIMED_OUT = "timed_out"

@dataclass
class CommandInfo:
    command_id: str
    command: str
    description: str
    thread_id: str
    status: CommandStatus
    started_at: datetime
    completed_at: datetime | None
    return_code: int | None
    pid: int | None               # 系统进程 ID，用于持久化和孤儿检测
    _process: subprocess.Popen    # 运行时引用（不持久化）
    _output: str                  # 累积输出
    _reader_thread: threading.Thread
    _lock: threading.Lock
```

### 核心方法

| 方法 | 说明 |
|------|------|
| `start(command, description, shell, thread_id)` | 启动后台命令，返回 command_id |
| `get_output(command_id)` | 获取命令状态和全量输出 |
| `kill(command_id)` | 终止命令（SIGTERM → SIGKILL 进程组） |
| `list_commands(thread_id)` | 列出后台命令摘要 |
| `cleanup(command_id)` | 从注册表移除 |
| `cleanup_by_thread(thread_id)` | 清理某线程的所有命令 |

### 输出读取机制

每个后台命令启动一个 daemon reader thread，从 `Popen` pipe 循环 `readline()`，同时写入日志文件。`get_output()` 直接读取日志文件，无需阻塞。

**日志文件路径**: `{base_dir}/threads/{thread_id}/cmd_logs/{command_id}.log`

所有 stdout/stderr 输出重定向到日志文件，服务重启后输出内容仍然可读。

## 2. 状态持久化（已实现 — 策略 B：日志文件重定向）

### 持久化文件

**元数据**: `{base_dir}/threads/{thread_id}/background_commands.json`
**输出日志**: `{base_dir}/threads/{thread_id}/cmd_logs/{command_id}.log`

```json
{
  "commands": [
    {
      "command_id": "cmd_abc123",
      "command": "npx vite --host 0.0.0.0 --port 3000",
      "description": "Start dev server",
      "status": "running",
      "pid": 12345,
      "started_at": "2026-04-11T09:01:28+00:00",
      "completed_at": null,
      "return_code": null
    }
  ]
}
```

### 写入时机

每次以下操作后写入文件：
- `start()` — 新命令启动
- `kill()` — 命令被终止
- `cleanup()` — 命令被清理
- reader thread 检测到进程结束 — 状态变为 completed/failed

### 加载与恢复（已实现）

**首次调用 `start()` 时**（延迟加载）：
1. 扫描 `{base_dir}/threads/*/background_commands.json`
2. 加载所有命令元数据到内存注册表
3. 对 `status == "running"` 的命令：
   - 检查 PID 是否存活（`os.kill(pid, 0)`）
   - 存活 → 保留 `running` 状态，输出从日志文件读取，可通过 `bash_kill` 终止
   - 不存活 → 标记为 `FAILED`，更新持久化文件
4. 对已结束的命令，加载元数据供 `bash_list` 查询

### 输出日志重定向（已实现）

- 后台命令的 stdout/stderr 由 reader thread 同时写入日志文件
- `get_output()` 从日志文件读取，重启后输出仍可查看
- `bash_kill` 可终止孤儿进程（通过 PID 的进程组）
- 日志文件: `{base_dir}/threads/{thread_id}/cmd_logs/{command_id}.log`

## 3. 新增工具

**文件**: `deerflow/sandbox/tools.py`

| 工具 | 参数 | 返回 |
|------|------|------|
| `bash_background` | description, command | command_id + 使用提示 |
| `bash_output` | command_id | status + output |
| `bash_kill` | command_id | kill result + final output |
| `bash_list` | (none) | 命令列表（含已结束的历史命令） |

## 4. 工具注册

**文件**: `config.yaml` + `config.example.yaml`

```yaml
tools:
  - name: bash
    group: bash
    use: deerflow.sandbox.tools:bash_tool
  - name: bash_background
    group: bash
    use: deerflow.sandbox.tools:bash_background_tool
  - name: bash_output
    group: bash
    use: deerflow.sandbox.tools:bash_output_tool
  - name: bash_kill
    group: bash
    use: deerflow.sandbox.tools:bash_kill_tool
  - name: bash_list
    group: bash
    use: deerflow.sandbox.tools:bash_list_tool
```

## 5. 生命周期管理

- **SandboxMiddleware.after_agent**: 清理该线程的所有后台命令
- **按 thread_id 隔离**: 每个命令关联 thread_id
- **服务重启**: 加载持久化文件，恢复命令状态

## 6. 配置

```yaml
sandbox:
  command_timeout: 120          # 同步命令超时（秒）
  max_background_commands: 10   # 每线程最大后台命令数
```

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `deerflow/sandbox/process_manager.py` | **新建** | 进程管理器 + 持久化 |
| `deerflow/sandbox/tools.py` | **修改** | 添加 4 个新工具 |
| `deerflow/tools/tools.py` | **修改** | 注册新工具 |
| `deerflow/sandbox/middleware.py` | **修改** | after_agent 清理 |
| `deerflow/config/sandbox_config.py` | **修改** | 添加 max_background_commands |
| `config.example.yaml` | **修改** | 添加新工具配置 |
| `config.yaml` | **修改** | 添加新工具配置 |

## 验证方法

1. 启动服务
2. AI 执行 `bash_background("npx vite --host 0.0.0.0")` → 立即返回 command_id
3. AI 调用 `bash_list` → 显示运行中的命令
4. AI 调用 `bash_output(cmd_id)` → 显示 dev server 输出
5. AI 调用 `bash_kill(cmd_id)` → 终止 dev server
6. 验证 `background_commands.json` 已更新
7. 重启服务，验证命令状态被恢复
8. 验证进程清理：`ps aux | grep vite` 无残留
