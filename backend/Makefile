# Windows (Git Bash/MSYS): uvicorn's default loop bypasses the SelectorEventLoop
# policy set by backend/sitecustomize.py and forces ProactorEventLoop, which hangs
# psycopg async (postgres checkpointer + app DB pool) until PoolTimeout. --loop none
# defers to the policy. No-op on Linux/macOS (keeps uvloop). Mirrors scripts/serve.sh.
UVICORN_LOOP := $(shell uname -s 2>/dev/null | grep -qiE '^(MINGW|MSYS|CYGWIN)' && echo --loop none)

install:
	uv sync

dev:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 --reload $(UVICORN_LOOP)

gateway:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001 $(UVICORN_LOOP)

test:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/ -v

test-blocking-io:
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run pytest tests/blocking_io -q --tb=short

lint:
	uvx ruff check .
	uvx ruff format --check .

format:
	uvx ruff check . --fix && uvx ruff format .

detect-blocking-io:
	@PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python ../scripts/detect_blocking_io_static.py --output ../.deer-flow/blocking-io-findings.json

# Generate a new alembic revision by autogenerating the diff against the live
# ORM models. Usage: make migrate-rev MSG="add foo column to runs"
# The Gateway runs `alembic upgrade head` automatically at startup via
# `bootstrap_schema`, so there is intentionally no `migrate` / `migrate-stamp`
# target -- the single execution path keeps ops mistakes off the table.
# The script builds a fresh temp SQLite at head, then diffs the live models
# against it, so a clean checkout does NOT need a pre-existing ./data/deerflow.db.
migrate-rev:
	@if [ -z "$(MSG)" ]; then echo 'usage: make migrate-rev MSG="describe the change"'; exit 1; fi
	PYTHONPATH=. PYTHONIOENCODING=utf-8 PYTHONUTF8=1 uv run python scripts/_autogen_revision.py "$(MSG)"
