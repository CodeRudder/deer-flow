# Bash 命令超时与进程管理

## Context

主会话中 AI 调用 `bash` 工具启动 Vite dev server（`npx vite`），dev server 是常驻进程，`subprocess.run()` 永远阻塞。Run 卡在 `tools` 节点 12 分钟，产生两组孤儿进程。

**根因**：sandbox `execute_command` 使用 `subprocess.run(timeout=600)`，超时不杀子进程（`subprocess.TimeoutExpired` 不清理进程树），且 10 分钟太长。

**目标**：
1. 命令超时后自动杀进程树，返回已收集的输出
2. 超时不可禁用，最小 10s
3. 为后续 AI 进程管理（后台执行、查询、终止）预留设计

---

## 1. 超时机制（已实现）

### 配置

**文件**: `backend/packages/harness/deerflow/config/sandbox_config.py`

```python
command_timeout: int = Field(
    default=120,
    ge=10,
    description="Timeout in seconds for bash command execution (default: 120). "
                "On timeout the process tree is killed and partial output is returned. "
                "Minimum: 10s.",
)
```

用户可在 `config.yaml` 中配置：

```yaml
sandbox:
  command_timeout: 120  # 默认 120s，最小 10s
```

### 执行改造

**文件**: `backend/packages/harness/deerflow/sandbox/local/local_sandbox.py`

将 `execute_command` 拆分为 Unix/Windows 路径：

- **Unix**: 使用 `subprocess.Popen` + `preexec_fn=os.setsid` 创建进程组
  - `proc.communicate(timeout=timeout)` 等待完成
  - 超时后：`os.killpg(pgid, SIGTERM)` → 等 3s → `SIGKILL` → 收集剩余输出
  - 返回部分输出 + `[Command timed out after {timeout}s and was terminated]`
- **Windows**: 保持 `subprocess.run`（超时会杀进程），传入 timeout

新增辅助方法：
- `_get_command_timeout()` — 从 config 读取超时，默认 120s
- `_kill_process_tree(proc)` — SIGTERM → 3s → SIGKILL 进程组
- `_collect_output(proc)` — 收集被杀进程的残留输出

### 无需修改的文件

- `bash_tool` (`sandbox/tools.py`) — 已有 `except Exception` 捕获，超时信息通过 `execute_command` 的正常返回值传递
- `Sandbox` 抽象基类 — 签名不变
- `LocalSandboxProvider` — 无变更

---

## 2. 进程管理机制（预留设计，暂不实现）

参考 Claude Code 的 bash 执行模式和 subagent background task 模式。

### 设计思路

在 `LocalSandbox` 中添加进程注册表，支持后台命令执行：

```python
# LocalSandbox 新增
_running_processes: dict[str, subprocess.Popen]  # cmd_id -> Popen
_output_buffers: dict[str, str]                   # cmd_id -> 已收集输出
```

### 新增工具（未来）

| 工具 | 说明 |
|------|------|
| `bash_background` | 启动后台命令，立即返回 cmd_id |
| `bash_output` | 获取后台命令的输出（从上次读取位置） |
| `bash_status` | 查询命令运行状态 |
| `bash_kill` | 终止后台命令 |

### 执行流程（未来）

```
AI 调用 bash_background → 返回 cmd_id
AI 继续其他工作
AI 调用 bash_output(cmd_id) → 获取当前输出
AI 调用 bash_kill(cmd_id) → 终止后台进程
```

### 与现有模式的关系

- 复用 subagent `executor.py` 的 background task 模式（线程池 + 状态追踪 + 超时清理）
- 进程生命周期绑定 sandbox 生命周期（`SandboxMiddleware.release` 时清理）
- 进程注册表线程安全（`threading.Lock`）

---

## 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `deerflow/config/sandbox_config.py` | 修改 | 添加 `command_timeout` 字段 |
| `deerflow/sandbox/local/local_sandbox.py` | 修改 | Popen + 进程组 + 超时杀树 |

## 验证方法

1. 启动服务
2. 在 Web Chat 中发送消息触发 AI 执行 `npx vite`（或 `sleep 200`）
3. 验证超时后命令被终止，返回部分输出 + timeout 提示
4. 验证无孤儿进程：`ps aux | grep vite` 应无残留
5. 修改 `config.yaml` 中 `command_timeout: 30`，重启，验证 30s 超时生效
