# DeerFlow TUI — 终端交互式会话管理工具

## Context

开发者在终端中管理 DeerFlow 会话时，需要反复调用 `thread-tasks.py` 查看状态，无法快速浏览、切换和操作多个会话。需要一个交互式 TUI 工具，支持导航进入/退出会话和子任务，查看消息，以及带确认的停止操作。

## 技术方案

- **框架**: `textual` (async TUI，内置 Rich 渲染，CSS 主题，CJK 原生支持)
- **HTTP**: `httpx` (async client，连接池)
- **文件**: 单文件 `scripts/deerflow-tui.py`
- **运行**: `uv run scripts/deerflow-tui.py`（PEP 723 内联依赖声明，无需预装）

## 屏幕架构（三级导航）

```
ThreadListScreen → ThreadDetailScreen → MessageViewerScreen
     (Escape 返回)        (Escape 返回)
```

### Screen 1: ThreadListScreen（根页面）
- 调用 `POST /api/threads/search` 获取线程列表
- DataTable 显示: 标题、状态、更新时间、ID（截断）
- 按更新时间倒序排列
- `Enter` 进入 ThreadDetailScreen
- `/` 过滤线程标题

### Screen 2: ThreadDetailScreen（会话详情）
- 数据源:
  - `GET /api/threads/{id}/state` — todos、标题、artifacts
  - `GET /api/threads/{id}/status` — 主会话状态、活跃子任务
  - `GET /api/threads/{id}/subagents?limit=50` — 子任务列表
- 布局:
  - 头部: 标题、ID、主会话状态
  - Todos 面板（可折叠）: 状态图标 + 内容
  - 子任务 DataTable: ID、Agent、描述、状态、开始时间、最后更新、消息数
- `Enter` 打开选中子任务的消息
- `m` 查看主会话消息
- `s` 停止主会话（弹出确认）
- `c` 取消选中子任务（弹出确认）

### Screen 3: MessageViewerScreen（消息查看）
- 两种模式:
  - **子任务模式**: `GET /api/threads/{id}/subagents/{task_id}` 获取消息
  - **会话模式**: `GET /api/threads/{id}/messages?limit=&offset=` 分页获取
- 消息渲染: 角色标签（Human/AI/Tool）+ 时间戳 + 内容
- `n`/`p` 翻页（会话模式）
- `c` 取消子任务（子任务模式，弹出确认）

## 键位绑定

| 键 | 位置 | 动作 |
|---|---|---|
| `q` | 全局 | 退出 |
| `?` | 全局 | 帮助 |
| `Escape` | 所有 | 返回上一级 |
| `Enter` | 列表 | 进入详情 |
| `j/k` 或方向键 | 列表/消息 | 移动光标/滚动 |
| `r` | 详情 | 刷新数据 |
| `/` | 线程列表 | 过滤标题 |
| `m` | 详情 | 查看主会话消息 |
| `s` | 详情 | 停止会话（确认） |
| `c` | 详情/消息 | 取消子任务（确认） |

## 确认对话框

`ConfirmDialog(ModalScreen)` — 居中弹出，显示标题和描述，`Y` 确认 / `N` 或 `Escape` 取消。

使用模式:
```python
self.app.push_screen(
    ConfirmDialog("停止会话", "确定要停止主会话运行？"),
    lambda confirmed: confirmed and self.run_worker(self._do_stop()),
)
```

## 自动刷新

- ThreadDetailScreen: 有 running 状态时每 5 秒刷新 `/api/threads/{id}/status`
- MessageViewerScreen (子任务模式): 子任务 running 时每 5 秒追加新消息
- 显示 `[LIVE]` 指示器表示自动刷新中

## 错误处理

- API 失败: `notify(error, severity="error")` toast 提示
- 连接失败: 全屏错误提示，显示服务地址和启动指引
- 空状态: 居中提示文字（如 "没有子任务"）

## 实现顺序

1. **脚手架**: 文件头、PEP 723 元数据、空 App、CLI 参数
2. **API Client**: `DeerFlowClient` 类，封装所有 HTTP 调用
3. **ThreadListScreen**: 线程列表 DataTable + 导航
4. **ThreadDetailScreen**: 完整布局（todos + 子任务表）
5. **MessageViewerScreen**: 双模式消息查看 + 分页
6. **确认对话框 + 操作**: ConfirmDialog、停止会话、取消子任务
7. **自动刷新**: 运行状态轮询
8. **打磨**: 面包屑、过滤、帮助、loading 状态

## 关键文件引用

- `scripts/thread-tasks.py` — API URL、状态颜色、时间格式参考
- `backend/app/gateway/routers/threads.py` — 线程 API 响应模型
- `backend/app/gateway/routers/subagents.py` — 子任务 API 响应模型
- `backend/app/gateway/routers/runs.py` — 取消子任务端点
- `backend/app/gateway/routers/thread_runs.py` — RunResponse、取消 run 端点

## 验证方式

1. `uv run scripts/deerflow-tui.py` 启动 TUI
2. 浏览线程列表，进入线程详情，查看子任务和消息
3. 测试停止会话和取消子任务的确认流程
4. 验证 running 状态自动刷新
5. 测试 `--gateway` 和 `--langgraph` 参数覆盖
