#!/usr/bin/env bash
#
# status.sh — Check DeerFlow service status
#
# Usage:
#   ./scripts/status.sh

set -euo pipefail

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[0;33m'
NC='\033[0m'

check_port() {
    local name="$1" port="$2"
    if command -v lsof >/dev/null 2>&1; then
        if lsof -nP -iTCP:"$port" -sTCP:LISTEN -t >/dev/null 2>&1; then
            echo -e "  ${GREEN}●${NC} $name (port $port)"
            return 0
        fi
    elif command -v ss >/dev/null 2>&1; then
        if ss -ltn "( sport = :$port )" 2>/dev/null | tail -n +2 | grep -q .; then
            echo -e "  ${GREEN}●${NC} $name (port $port)"
            return 0
        fi
    fi
    echo -e "  ${RED}○${NC} $name (port $port) — not running"
    return 1
}

check_http() {
    local name="$1" url="$2"
    if curl -sf -o /dev/null -m 2 "$url" 2>/dev/null; then
        echo -e "    ${GREEN}✓${NC} HTTP OK"
    else
        echo -e "    ${RED}✗${NC} HTTP not responding"
    fi
}

echo ""
echo "DeerFlow Service Status"
echo "======================="

ALL_UP=true

# Check LangGraph
if check_port "LangGraph" 2024; then
    check_http "LangGraph" "http://localhost:2024/ok"
else
    ALL_UP=false
fi

# Check Gateway
if check_port "Gateway" 8001; then
    check_http "Gateway" "http://localhost:8001/health"
else
    ALL_UP=false
fi

# Check Frontend
if check_port "Frontend" 2025; then
    :
else
    ALL_UP=false
fi

# Check Nginx
if check_port "Nginx" 2026; then
    check_http "Nginx" "http://localhost:2026/"
else
    ALL_UP=false
fi

echo ""
if $ALL_UP; then
    echo -e "Overall: ${GREEN}All services running${NC}"
    echo "  URL: http://localhost:2026"
else
    echo -e "Overall: ${YELLOW}Some services not running${NC}"
    echo "  Start: ./scripts/start.sh"
fi
echo ""
