#!/usr/bin/env bash
set -Eeuo pipefail

# Safe SAS PRO housekeeping. Never delete databases, Docker volumes, .env, or user uploads.
APP_DIR="/opt/saspro"
LOCK_FILE="/run/lock/saspro-cleanup.lock"
LOG_TAG="[saspro-cleanup]"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "$LOG_TAG another cleanup run is active; skipping"
  exit 0
fi

echo "$LOG_TAG started at $(date -Is)"

# Generated Python caches/bytecode can always be recreated.
if [[ -d "$APP_DIR/app" ]]; then
  find "$APP_DIR/app" -type d -name '__pycache__' -prune -exec rm -rf -- {} + 2>/dev/null || true
  find "$APP_DIR/app" -type f \( -name '*.pyc' -o -name '*.pyo' \) -delete 2>/dev/null || true
fi

# Only remove known SAS health-check leftovers and SAS-prefixed temporary files,
# and only after 48 hours. Do not sweep generic /tmp files.
find /tmp -maxdepth 1 -type f \(
  -name 'saspro-*' -o -name 'saspro_health.out' -o -name 'saspro_health.err'
\) -mmin +2880 -delete 2>/dev/null || true

# Reclaim dangling images and builder cache older than seven days only.
# Never run docker system prune -a or volume prune: persistent data must survive.
if command -v docker >/dev/null 2>&1; then
  docker image prune -f >/dev/null 2>&1 || true
  docker builder prune -f --filter 'until=168h' >/dev/null 2>&1 || true
fi

echo "$LOG_TAG finished at $(date -Is)"
