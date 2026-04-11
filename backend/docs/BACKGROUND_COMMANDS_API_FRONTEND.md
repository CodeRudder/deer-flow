# 后台命令管理 — Gateway API 与前端组件设计

## Context

ProcessManager（`deerflow/sandbox/process_manager.py`）已实现后台命令的启动、输出读取、终止、持久化等功能。AI 通过 `bash_background`、`bash_output`、`bash_kill`、`bash_list` 四个工具管理后台命令。

**目标**：为 Web 前端提供 Gateway API 端点，支持用户在聊天界面查看和管理当前会话的后台命令。

---

## 1. Gateway API

### 路由文件

**新建**: `app/gateway/routers/commands.py`

路由前缀: `/api/threads/{thread_id}/commands`

遵循现有 routers 的组织模式（Pydantic request/response models + APIRouter）。

### 端点设计

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/api/threads/{thread_id}/commands` | 列出该线程的所有后台命令 |
| `GET` | `/api/threads/{thread_id}/commands/{command_id}/output` | 获取命令输出（分页） |
| `POST` | `/api/threads/{thread_id}/commands/{command_id}/kill` | 终止后台命令 |

### 1.1 列出命令

```
GET /api/threads/{thread_id}/commands
```

**Response** (`CommandListResponse`):

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
      "return_code": null
    }
  ]
}
```

**实现**: 调用 `process_manager.list_commands(thread_id=thread_id)`。

### 1.2 获取输出

```
GET /api/threads/{thread_id}/commands/{command_id}/output?start_line=0&line_count=20
```

**Query Parameters**:

| 参数 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `start_line` | int (optional) | null | 起始行号（0-based），null 表示从末尾读取 |
| `line_count` | int | 10 | 读取行数，最大 50 |

**Response** (`CommandOutputResponse`):

```json
{
  "command_id": "cmd_abc123",
  "status": "running",
  "output": "Total lines: 200, showing lines 1-20 (start_line=0, line_count=20), 180 lines after\n\nLine 1\nLine 2...",
  "log_file": "/path/to/cmd_abc123.log",
  "pagination": {
    "total_lines": 200,
    "start_line": 0,
    "line_count": 20,
    "has_more": true
  }
}
```

**实现**: 调用 `process_manager.get_output(command_id, start_line, line_count)`，解析返回值构建结构化响应。

### 1.3 终止命令

```
POST /api/threads/{thread_id}/commands/{command_id}/kill
```

**Response** (`CommandKillResponse`):

```json
{
  "killed": true,
  "message": "Command cmd_abc123 killed.",
  "final_output": "..."
}
```

**实现**: 调用 `process_manager.kill(command_id)`。

### Pydantic Models

```python
class CommandItem(BaseModel):
    command_id: str
    command: str
    description: str
    status: str
    pid: int | None
    started_at: str
    return_code: int | None

class CommandListResponse(BaseModel):
    commands: list[CommandItem]

class PaginationInfo(BaseModel):
    total_lines: int
    start_line: int
    line_count: int
    has_more: bool

class CommandOutputResponse(BaseModel):
    command_id: str
    status: str
    output: str
    log_file: str | None
    pagination: PaginationInfo

class CommandKillResponse(BaseModel):
    killed: bool
    message: str
    final_output: str | None = None
```

### 注册路由

在 `app/gateway/app.py` 中:

```python
from app.gateway.routers import commands
app.include_router(commands.router)
```

在 `app.py` 的 `openapi_tags` 中添加:

```python
{"name": "commands", "description": "Manage background commands for threads"},
```

---

## 2. 前端组件

### 2.1 组件位置

`frontend/src/components/workspace/background-commands/`

### 2.2 组件设计

#### `BackgroundCommandsIndicator` — 浮动指示器

**位置**: `frontend/src/components/workspace/background-commands-indicator.tsx`

**行为**:
- 固定在右下角的浮动按钮（类似 `ActiveRunsIndicator`）
- 仅当存在 `running` 状态的后台命令时显示
- 显示运行中的命令数量 badge
- 点击展开 Sheet 侧边栏

**轮询**:
- 每 5 秒调用 `GET /api/threads/{thread_id}/commands` 刷新数据
- 当前线程 ID 从 URL 参数获取（`useParams`）

**Sheet 内容**:
- 命令列表，每个命令一个 `CommandCard`
- "Stop All" 按钮（批量终止）

#### `CommandCard` — 命令卡片

**位置**: `frontend/src/components/workspace/background-commands/command-card.tsx`

**显示内容**:
- 命令描述 + 截断的命令文本
- 状态图标（running: 旋转 Loader, completed: 绿色 Check, failed: 红色 X, killed: 灰色 Square）
- 启动时间（相对时间）
- 操作按钮：
  - 运行中：显示 Stop 按钮（SquareIcon）
  - 已完成/失败：显示 View Output 按钮

#### `CommandOutputDialog` — 输出查看弹窗

**位置**: `frontend/src/components/workspace/background-commands/command-output-dialog.tsx`

**行为**:
- Dialog 弹窗，显示命令的完整输出
- 分页控制（上一页 / 下一页）
- 显示元数据：总行数、当前区间
- 实时刷新（running 状态时每 3 秒自动刷新）

### 2.3 API 调用

所有 API 调用使用 `getBackendBaseURL()` 构建请求：

```typescript
const baseUrl = getBackendBaseURL();
const threadId = params.thread_id;

// 列出命令
fetch(`${baseUrl}/api/threads/${threadId}/commands`)

// 获取输出
fetch(`${baseUrl}/api/threads/${threadId}/commands/${commandId}/output?start_line=0&line_count=20`)

// 终止命令
fetch(`${baseUrl}/api/threads/${threadId}/commands/${commandId}/kill`, { method: "POST" })
```

### 2.4 类型定义

```typescript
interface BackgroundCommand {
  command_id: string;
  command: string;
  description: string;
  status: "running" | "completed" | "failed" | "killed" | "timed_out";
  pid: number | null;
  started_at: string;
  return_code: number | null;
}

interface CommandOutput {
  command_id: string;
  status: string;
  output: string;
  log_file: string | null;
  pagination: {
    total_lines: number;
    start_line: number;
    line_count: number;
    has_more: boolean;
  };
}

interface KillResult {
  killed: boolean;
  message: string;
  final_output: string | null;
}
```

### 2.5 集成点

在 `frontend/src/app/workspace/chats/[thread_id]/layout.tsx` 或页面组件中添加：

```tsx
<BackgroundCommandsIndicator threadId={threadId} />
```

与 `ActiveRunsIndicator` 并列，作为全局浮动组件。

---

## 3. 关键文件

| 文件 | 操作 | 说明 |
|------|------|------|
| `app/gateway/routers/commands.py` | **新建** | Gateway API 路由 |
| `app/gateway/app.py` | **修改** | 注册 commands router |
| `frontend/src/components/workspace/background-commands-indicator.tsx` | **新建** | 浮动指示器 |
| `frontend/src/components/workspace/background-commands/command-card.tsx` | **新建** | 命令卡片 |
| `frontend/src/components/workspace/background-commands/command-output-dialog.tsx` | **新建** | 输出查看弹窗 |

---

## 4. 实现顺序

1. **Gateway API** — `commands.py` 路由 + 注册
2. **Frontend 类型** — TypeScript 接口定义
3. **CommandCard** — 单个命令卡片组件
4. **CommandOutputDialog** — 输出查看弹窗
5. **BackgroundCommandsIndicator** — 浮动指示器 + 轮询
6. **集成** — 添加到 workspace 布局
