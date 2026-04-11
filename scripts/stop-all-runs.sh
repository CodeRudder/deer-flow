#!/usr/bin/env bash
# Cancel all active runs across all threads.
# Does NOT delete threads — only cancels pending/running/interrupted runs.
#
# Usage:
#   ./scripts/stop-all-runs.sh           # Cancel active runs
#   ./scripts/stop-all-runs.sh --list    # Only list active runs
#
# Requires: curl, python3

set -euo pipefail

LANGGRAPH_URL="${LANGGRAPH_URL:-http://localhost:2024}"

# ── Colors ─────────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m' # No Color

# ── Parse args ─────────────────────────────────────────────────────────────────
LIST_ONLY=false
if [[ "${1:-}" == "--list" ]]; then
    LIST_ONLY=true
fi

# ── Get all threads ────────────────────────────────────────────────────────────
threads=$(curl -s -X POST "$LANGGRAPH_URL/threads/search" \
    -H "Content-Type: application/json" \
    -d '{"limit": 100}' 2>/dev/null | python3 -c "
import sys, json
try:
    for t in json.load(sys.stdin):
        print(t['thread_id'])
except: pass
" 2>/dev/null)

if [[ -z "$threads" ]]; then
    echo -e "${GREEN}No threads found.${NC}"
    exit 0
fi

# ── Find and process active runs ───────────────────────────────────────────────
total_active=0
total_stopped=0
total_failed=0

for tid in $threads; do
    active_runs=$(curl -s "$LANGGRAPH_URL/threads/$tid/runs" 2>/dev/null | python3 -c "
import sys, json
try:
    runs = json.load(sys.stdin)
    for r in runs:
        s = r.get('status', '')
        if s in ('pending', 'running', 'interrupted'):
            print(r['run_id'], s)
except: pass
" 2>/dev/null)

    [[ -z "$active_runs" ]] && continue

    while IFS=' ' read -r rid status; do
        total_active=$((total_active + 1))
        short_tid="${tid:0:8}"
        short_rid="${rid:0:8}"

        if $LIST_ONLY; then
            echo -e "${YELLOW}Thread $short_tid... → Run $short_rid... ($status)${NC}"
            continue
        fi

        # Cancel the run
        echo -n "Stopping thread $short_tid... run $short_rid... ($status) → "
        result=$(curl -s -X POST "$LANGGRAPH_URL/threads/$tid/runs/$rid/cancel" 2>/dev/null)
        echo -e "${GREEN}cancelled${NC}"
        total_stopped=$((total_stopped + 1))
    done <<< "$active_runs"
done

# ── Summary ────────────────────────────────────────────────────────────────────
if [[ $total_active -eq 0 ]]; then
    echo -e "${GREEN}No active runs found.${NC}"
else
    if $LIST_ONLY; then
        echo ""
        echo "Total active runs: $total_active"
        echo "Run without --list to stop them."
    else
        echo ""
        echo -e "Stopped: ${GREEN}$total_stopped${NC} / $total_active runs"
    fi
fi
