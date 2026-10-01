#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="/opt/saspro"
BRANCH="main"
LOCK_FILE="/run/lock/saspro-sync.lock"
HEALTH_URL="http://127.0.0.1:8000/health"

exec 9>"$LOCK_FILE"
flock -n 9 || exit 0

cd "$APP_DIR"

echo "[$(date -Is)] SAS PRO sync check"

git fetch --quiet --prune origin "$BRANCH"

LOCAL_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(git rev-parse "origin/$BRANCH")"

if [[ "$LOCAL_SHA" == "$REMOTE_SHA" ]]; then
  echo "[$(date -Is)] Already up to date: ${LOCAL_SHA:0:12}"
  exit 0
fi

echo "[$(date -Is)] Update detected: ${LOCAL_SHA:0:12} -> ${REMOTE_SHA:0:12}"

git reset --hard "origin/$BRANCH"

echo "[$(date -Is)] Rebuilding production containers"
docker compose up -d --build --remove-orphans

echo "[$(date -Is)] Removing unused Docker images"
docker image prune -f >/dev/null 2>&1 || true

echo "[$(date -Is)] Waiting for health"
healthy=0
for _ in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/tmp/saspro-health.json 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [[ "$healthy" -ne 1 ]]; then
  echo "[$(date -Is)] ERROR: SAS PRO health check failed"
  docker compose ps
  docker compose logs --tail=120 saspro
  exit 1
fi

echo "[$(date -Is)] Deployment successful: $(git rev-parse --short HEAD)"
cat /tmp/saspro-health.json
