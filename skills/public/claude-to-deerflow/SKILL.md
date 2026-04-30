---
name: claude-to-deerflow
description: "Interact with DeerFlow AI agent platform via its HTTP API. Use this skill when the user wants to send messages or questions to DeerFlow for research/analysis, start a DeerFlow conversation thread, check DeerFlow status or health, list available models/skills/agents in DeerFlow, manage DeerFlow memory, upload files to DeerFlow threads, or delegate complex research tasks to DeerFlow. Also use when the user mentions deerflow, deer flow, or wants to run a deep research task that DeerFlow can handle."
---

# DeerFlow Skill

Communicate with a running DeerFlow instance via its HTTP API. DeerFlow is an AI agent platform
built on LangGraph that orchestrates sub-agents for research, code execution, web browsing, and more.

## Architecture

DeerFlow exposes two API surfaces behind an Nginx reverse proxy:

| Service        | Direct Port | Via Proxy                        | Purpose                          |
|----------------|-------------|----------------------------------|----------------------------------|
| Gateway API    | 8001        | `$DEERFLOW_GATEWAY_URL`          | REST endpoints (models, skills, memory, uploads) |
| LangGraph API  | 2024        | `$DEERFLOW_LANGGRAPH_URL`        | Agent threads, runs, streaming   |

## Environment Variables

All URLs are configurable via environment variables. **Read these env vars before making any request.**

| Variable                | Default                                  | Description                        |
|-------------------------|------------------------------------------|------------------------------------|
| `DEERFLOW_URL`          | `http://localhost:2026`                  | Unified proxy base URL             |
| `DEERFLOW_GATEWAY_URL`  | `${DEERFLOW_URL}`                        | Gateway API base (models, skills, memory, uploads) |
| `DEERFLOW_LANGGRAPH_URL`| `${DEERFLOW_URL}/api/langgraph`          | LangGraph API base (threads, runs) |

When making curl calls, always resolve the URL like this:

```bash
# Resolve base URLs from env (do this FIRST before any API call)
DEERFLOW_URL="${DEERFLOW_URL:-http://localhost:2026}"
DEERFLOW_GATEWAY_URL="${DEERFLOW_GATEWAY_URL:-$DEERFLOW_URL}"
DEERFLOW_LANGGRAPH_URL="${DEERFLOW_LANGGRAPH_URL:-$DEERFLOW_URL/api/langgraph}"
```

## Available Operations

### 1. Health Check

Verify DeerFlow is running:

```bash
curl -s "$DEERFLOW_GATEWAY_URL/health"
```

### 2. Send a Message (Streaming)

This is the primary operation. It creates a thread and streams the agent's response.

**Step 1: Create a thread**

```bash
curl -s -X POST "$DEERFLOW_LANGGRAPH_URL/threads" \
  -H "Content-Type: application/json" \
  -d '{}'
```

Response: `{"thread_id": "<uuid>", ...}`

**Step 2: Stream a run**

```bash
curl -s -N -X POST "$DEERFLOW_LANGGRAPH_URL/threads/<thread_id>/runs/stream" \
  -H "Content-Type: application/json" \
  -d '{
    "assistant_id": "lead_agent",
    "input": {
      "messages": [
        {
          "type": "human",
          "content": [{"type": "text", "text": "YOUR MESSAGE HERE"}]
        }
      ]
    },
    "stream_mode": ["values", "messages-tuple"],
    "stream_subgraphs": true,
    "config": {
      "recursion_limit": 1000
    },
    "context": {
      "thinking_enabled": true,
      "is_plan_mode": true,
      "subagent_enabled": true,
      "thread_id": "<thread_id>"
    }
  }'
```

The response is an SSE stream. Each event has the format:
```
event: <event_type>
data: <json_data>
```

Key event types:
- `metadata` — run metadata including `run_id`
- `values` — full state snapshot with `messages` array
- `messages-tuple` — incremental message updates (AI text chunks, tool calls, tool results)
- `end` — stream is complete

**Context modes** (set via `context`):
- Flash mode: `thinking_enabled: false, is_plan_mode: false, subagent_enabled: false`
- Standard mode: `thinking_enabled: true, is_plan_mode: false, subagent_enabled: false`
- Pro mode: `thinking_enabled: true, is_plan_mode: true, subagent_enabled: false`
- Ultra mode: `thinking_enabled: true, is_plan_mode: true, subagent_enabled: true`

### 3. Continue a Conversation

To send follow-up messages, reuse the same `thread_id` from step 2 and POST another run
with the new message.

### 4. List Models

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/models"
```

Returns: `{"models": [{"name": "...", "provider": "...", ...}, ...]}`

### 5. List Skills

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/skills"
```

Returns: `{"skills": [{"name": "...", "enabled": true, ...}, ...]}`

### 6. Enable/Disable a Skill

```bash
curl -s -X PUT "$DEERFLOW_GATEWAY_URL/api/skills/<skill_name>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

### 7. List Agents

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/agents"
```

Returns: `{"agents": [{"name": "...", ...}, ...]}`

### 8. Get Memory

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/memory"
```

Returns user context, facts, and conversation history summaries.

### 9. Upload Files to a Thread

```bash
curl -s -X POST "$DEERFLOW_GATEWAY_URL/api/threads/<thread_id>/uploads" \
  -F "files=@/path/to/file.pdf"
```

Supports PDF, PPTX, XLSX, DOCX — automatically converts to Markdown.

### 10. List Uploaded Files

```bash
curl -s "$DEERFLOW_GATEWAY_URL/api/threads/<thread_id>/uploads/list"
```

### 11. Get Thread History

```bash
curl -s "$DEERFLOW_LANGGRAPH_URL/threads/<thread_id>/history"
```

**Response format**: The API returns a JSON array directly (not wrapped in an object).

**Parsing response** (Python example):
```python
import sys, json
data = json.load(sys.stdin)
messages = data if isinstance(data, list) else data.get('messages', [])

for msg in messages:
    msg_type = msg.get('type', 'unknown')
    if msg_type == 'human':
        content = msg.get('content', '')
        if isinstance(content, list):
            content = ''.join([c.get('text', '') if isinstance(c, dict) else str(c) for c in content])
        print(f'👤 Human: {content}')
    elif msg_type == 'ai':
        content = msg.get('content', '')
        if isinstance(content, list):
            content = ''.join([c.get('text', '') if isinstance(c, dict) else str(c) for c in content])
        print(f'🤖 AI: {content}')
```

### 12. List Threads

```bash
curl -s -X POST "$DEERFLOW_LANGGRAPH_URL/threads/search" \
  -H "Content-Type: application/json" \
  -d '{"limit": 20, "sort_by": "updated_at", "sort_order": "desc"}'
```

**Response format**: The API returns a JSON array directly (not wrapped in an object).

**Parsing response** (Python example):
```python
import sys, json
data = json.load(sys.stdin)
threads = data if isinstance(data, list) else data.get('threads', [])

for i, t in enumerate(threads, 1):
    thread_id = t.get('thread_id', 'N/A')
    title = t.get('title', 'Untitled')
    # Thread title might be in values.title
    values = t.get('values', {})
    thread_title = values.get('title', title) if values else title
    status = t.get('status', 'unknown')
    todos = values.get('todos', []) if values else []
    artifacts = values.get('artifacts', []) if values else []
    
    print(f'{i}. {thread_id}')
    print(f'   Title: {thread_title}')
    print(f'   Status: {status}')
    print(f'   Todos: {len(todos)} items')
    if todos:
        for todo in todos:
            content = todo.get('content', 'N/A')
            todo_status = todo.get('status', 'unknown')
            print(f'     - [{todo_status}] {content}')
    if artifacts:
        for artifact in artifacts:
            print(f'     📄 {artifact}')
```

**Thread data structure**:
- `thread_id` — Unique thread identifier
- `title` — Thread title (fallback if values.title not present)
- `status` — Thread status: idle, busy, error
- `values` — Thread state containing:
  - `title` — Actual thread title (overrides top-level title)
  - `todos` — Array of todo items with `content` and `status`
  - `artifacts` — Array of artifact file paths
- `created_at` — Thread creation timestamp
- `updated_at` — Thread last update timestamp

## Usage Script

For sending messages and collecting the full response, use the helper script:

```bash
bash /path/to/skills/claude-to-deerflow/scripts/chat.sh "Your question here"
```

See `scripts/chat.sh` for the implementation. The script:
1. Checks health
2. Creates a thread
3. Streams the run and collects the final AI response
4. Prints the result

## Parsing SSE Output

The stream returns SSE events. To extract the final AI response from a `values` event:
- Look for the last `event: values` block
- Parse its `data` JSON
- The `messages` array contains all messages; the last one with `type: "ai"` is the response
- The `content` field of that message is the AI's text reply

## Error Handling

- If health check fails, DeerFlow is not running. Inform the user they need to start it.
- If the stream returns an error event, extract and display the error message.
- Common issues: port not open, services still starting up, config errors.

## Tips

- For quick questions, use flash mode (fastest, no planning).
- For research tasks, use pro or ultra mode (enables planning and sub-agents).
- You can upload files first, then reference them in your message.
- Thread IDs persist — you can return to a conversation later.
