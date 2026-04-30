# Resource Limits & SQLite Memory Optimization

## Overview

DeerFlow processes run with cgroup v2 resource limits to prevent any single service from exhausting system resources. Additionally, SQLite checkpoint storage is tuned to minimize page cache memory usage.

## Process Architecture

| Process | Port | Scope | Memory | CPU | IO Write |
|---------|------|-------|--------|-----|----------|
| LangGraph Server | 2024 | `deerflow-langgraph.scope` | 6 GB | 3 cores (300%) | 50 MB/s |
| Gateway API | 8001 | `deerflow-gateway.scope` | 2 GB | 2 cores (200%) | 30 MB/s |
| Frontend (Next.js) | 2025 | `deerflow-frontend.scope` | 1 GB | 1 core (100%) | 10 MB/s |
| Nginx | 2026 | `deerflow.service` cgroup | — | — | — |

## Service Management

```bash
# Start/stop via systemd
sudo systemctl start deerflow
sudo systemctl stop deerflow
sudo systemctl restart deerflow
systemctl status deerflow

# Switch mode (dev/prod) via .env or override
echo "DEER_FLOW_MODE=dev" >> .env
sudo systemctl restart deerflow
```

## cgroup v2 Setup

### Prerequisites

- Linux kernel with cgroup v2 (`/sys/fs/cgroup/cgroup.controllers` exists)
- systemd 240+
- User slice controller delegation (one-time setup)

### Controller Delegation

The `cpu`, `io`, `memory` controllers must be delegated to user slices. A systemd drop-in makes this persistent across reboots:

**`/etc/systemd/system/user@.service.d/delegate.conf`**:
```ini
[Service]
Delegate=cpu io memory pids
```

After creating: `sudo systemctl daemon-reload`.

### How It Works

`scripts/serve.sh` wraps each service with `systemd-run --user --scope`:

```
systemd-run --user --scope \
  --unit=deerflow-langgraph \
  --property=MemoryMax=6G \
  --property=CPUQuota=300% \
  sh -c "langgraph dev ..."
```

IO write bandwidth is applied after scope creation via `systemctl --user set-property` (avoids shell quoting issues):

```
systemctl --user set-property deerflow-langgraph.scope \
  "IOWriteBandwidthMax=252:17 50000000"
```

The block device (`252:17`) is auto-detected from the filesystem hosting the project via `lsblk`.

### Configuration Overrides

All limits can be overridden via `.env` or environment variables:

```bash
# Memory
LANGGRAPH_MEMORY_MAX=6G       # LangGraph memory cap
GATEWAY_MEMORY_MAX=2G          # Gateway memory cap
FRONTEND_MEMORY_MAX=1G         # Frontend memory cap

# CPU (percentage of a single core, 300% = 3 cores)
LANGGRAPH_CPU_QUOTA=300%
GATEWAY_CPU_QUOTA=200%
FRONTEND_CPU_QUOTA=100%

# IO write bandwidth (per second, 0 = unlimited)
LANGGRAPH_IO_MAX=50M
GATEWAY_IO_MAX=30M
FRONTEND_IO_MAX=10M

# Disable cgroup limits entirely
DEER_FLOW_CGROUP=0
```

### Verification

```bash
# Check running scopes
systemctl --user list-units --type=scope | grep deerflow

# Check limits per scope
systemctl --user show deerflow-langgraph.scope | grep -E "MemoryMax|CPUQuota|IOWriteBand"

# Check actual memory usage
cat /sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/deerflow-langgraph.scope/memory.current | numfmt --to=iec
```

## SQLite Memory Optimization

### Problem

LangGraph stores all checkpoint data in a single SQLite database (`~/.deer-flow/checkpoints.db`). As conversations grow, the DB can reach several GB. Linux caches file pages in memory (page cache), and cgroup v2 counts this against `memory.current`. This caused LangGraph to appear to use 1.7 GB when its actual process memory (RSS) was only 180 MB.

Root cause breakdown:
- `anon` (process heap/stack): ~180 MB — normal
- `file` (OS page cache of DB): ~1.5 GB — charged to cgroup

### Solution: Three Layers

#### Layer 1: Checkpoint Cleanup (DB size control)

```yaml
# config.yaml
checkpoint_cleanup:
  enabled: true
  keep: 50           # Keep latest N checkpoints per thread chain
  interval: 3600     # Cleanup cycle in seconds
```

Implementation: `backend/app/gateway/checkpoint_cleaner.py`

- Walks the parent-checkpoint chain from each leaf, keeps the latest N
- Deletes old checkpoints and associated writes in batched SQL
- Drops orphaned threads (not in store but have checkpoint data)
- Runs `PRAGMA wal_checkpoint(TRUNCATE)` after cleanup to flush WAL
- Safety: leaf checkpoints are never deleted; empty keep-set aborts

Manual one-time cleanup + VACUUM:

```bash
cd backend
uv run python3 -c "
import asyncio, aiosqlite, os
async def clean():
    conn = await aiosqlite.connect(os.path.expanduser('~/.deer-flow/checkpoints.db'))
    # ... run CheckpointCleaner._cleanup_all() ...
    await conn.execute('VACUUM')
    await conn.close()
asyncio.run(clean())
"
```

#### Layer 2: SQLite PRAGMA Tuning

Applied in `packages/harness/deerflow/agents/checkpointer/async_provider.py` after connection creation:

```sql
PRAGMA cache_size=-8000;       -- Pager cache: 8 MB (default 2 MB)
PRAGMA mmap_size=67108864;     -- Memory-mapped IO: 64 MB cap
PRAGMA wal_autocheckpoint=1000; -- Flush WAL every 1000 pages (4 MB)
PRAGMA temp_store=MEMORY;      -- Temp tables in memory
```

**Key PRAGMA: `mmap_size`**

When SQLite uses `mmap()` to read the database file, the mapped pages are **not charged to the cgroup's `memory.current`**. This is the single most effective change for reducing reported memory usage:

| Setting | `memory.current` | Page cache (`file`) |
|---------|-----------------|-------------------|
| No `mmap_size` (default) | 1.7 GB | 1.5 GB |
| `mmap_size=64MB` | 185 MB | 3.8 MB |

#### Layer 3: cgroup Memory Limit (safety net)

Even with the above optimizations, `MemoryMax=6G` provides a hard cap to prevent runaway memory usage from killing the host system.

### Checkpoint Data Anatomy

Each checkpoint blob (msgpack) contains:

| Channel | Typical Size | Percentage |
|---------|-------------|-----------|
| `__pregel_tasks` | 3 MB | 75% |
| `messages` | 1 MB | 24% |
| `todos`/`title`/`artifacts` | <5 KB | <1% |

The `__pregel_tasks` channel is the largest component — it stores task execution state including intermediate results. Three tasks per checkpoint × ~1 MB each = 3 MB.

### Monitoring

```bash
# DB size
du -h ~/.deer-flow/checkpoints.db

# Checkpoint count per thread
sqlite3 ~/.deer-flow/checkpoints.db \
  "SELECT thread_id, COUNT(*) FROM checkpoints GROUP BY thread_id ORDER BY COUNT(*) DESC LIMIT 10"

# Current memory breakdown
CG="/sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service/app.slice/deerflow-langgraph.scope"
echo "total: $(cat $CG/memory.current | numfmt --to=iec)"
echo "anon:  $(cat $CG/memory.stat | grep '^anon ' | awk '{print $2}' | numfmt --to=iec)"
echo "cache: $(cat $CG/memory.stat | grep '^file ' | awk '{print $2}' | numfmt --to=iec)"
```
