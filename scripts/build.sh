#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

echo "Building DeerFlow..."
echo ""

# ── Frontend ─────────────────────────────────────────────────────────────

echo "→ Building frontend..."
cd "$PROJECT_DIR/frontend"

if command -v python3 >/dev/null 2>&1; then
    AUTH_SECRET=$(python3 -c 'import secrets; print(secrets.token_hex(16))')
elif command -v python >/dev/null 2>&1; then
    AUTH_SECRET=$(python -c 'import secrets; print(secrets.token_hex(16))')
else
    AUTH_SECRET="dev-secret-$(date +%s)"
fi

SKIP_ENV_VALIDATION=1 BETTER_AUTH_SECRET="$AUTH_SECRET" pnpm run build
echo "✓ Frontend build complete"
echo ""

# ── Backend (syntax check) ──────────────────────────────────────────────

echo "→ Checking backend..."
cd "$PROJECT_DIR/backend"
uv run python -m py_compile app/gateway/app.py
echo "✓ Backend check complete"
echo ""

echo "✓ Build complete"
