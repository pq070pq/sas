#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/saspro"
BRANCH="main"
LOCK_FILE="/run/lock/saspro-sync.lock"
LOG_PREFIX="[saspro-sync]"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$APP_DIR"

echo "$LOG_PREFIX checking GitHub..."
git fetch --prune origin "$BRANCH"

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
  echo "$LOG_PREFIX already up to date: ${LOCAL_SHA:0:12}"
  exit 0
fi

echo "$LOG_PREFIX updating ${LOCAL_SHA:0:12} -> ${REMOTE_SHA:0:12}"
git reset --hard "origin/$BRANCH"

# .env is intentionally untracked and is preserved by git reset.
# Secrets must never be committed to GitHub.
chmod 600 .env 2>/dev/null || true

echo "$LOG_PREFIX rebuilding Docker stack..."
docker compose up -d --build --remove-orphans

echo "$LOG_PREFIX waiting for health..."
for i in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/health >/tmp/saspro-health.json 2>/dev/null; then
    echo "$LOG_PREFIX deployment healthy"
    cat /tmp/saspro-health.json
    docker compose ps
    exit 0
  fi
  sleep 2
done

echo "$LOG_PREFIX health check failed"
docker compose ps || true
docker compose logs --tail=120 saspro || true
exit 1
