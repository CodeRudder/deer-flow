#!/usr/bin/env bash
# systemd-start.sh — DeerFlow service entrypoint for systemd
# Reads DEER_FLOW_MODE (dev/prod) from environment, defaults to prod.
set -e

MODE="${DEER_FLOW_MODE:-prod}"
REPO_ROOT="$(builtin cd "$(dirname "${BASH_SOURCE[0]}")/.." >/dev/null 2>&1 && pwd -P)"

# systemd-run --user requires a user session bus.
# When run from a system service (Type=forking), these are not set.
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=${XDG_RUNTIME_DIR}/bus"

exec "$REPO_ROOT/scripts/serve.sh" "--${MODE}" --daemon
