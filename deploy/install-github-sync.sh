#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/saspro"
SYNC="/usr/local/bin/saspro-github-sync"
SERVICE="/etc/systemd/system/saspro-github-sync.service"
TIMER="/etc/systemd/system/saspro-github-sync.timer"
REPO_URL="https://github.com/pq070pq/sas.git"
BRANCH="main"

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root."
  exit 1
fi

test -d "$APP_DIR/.git" || { echo "Missing $APP_DIR/.git"; exit 1; }
command -v git >/dev/null || { echo "git is required"; exit 1; }
command -v docker >/dev/null || { echo "docker is required"; exit 1; }

cd "$APP_DIR"

git remote get-url origin >/dev/null 2>&1 || git remote add origin "$REPO_URL"
git remote set-url origin "$REPO_URL"

cat > "$SYNC" <<'EOF'
#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/saspro"
REPO_URL="https://github.com/pq070pq/sas.git"
BRANCH="main"
LOCK="/run/saspro-github-sync.lock"
HEALTH_URL="http://127.0.0.1:8000/health"

exec 9>"$LOCK"
flock -n 9 || exit 0

cd "$APP_DIR"
git remote set-url origin "$REPO_URL"

git fetch --prune origin "$BRANCH"

LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"

if [ "$LOCAL" != "$REMOTE" ]; then
  echo "[$(date -Is)] New GitHub commit: $REMOTE"
  git reset --hard "origin/$BRANCH"

  docker compose up -d --build --remove-orphans
  docker image prune -f
else
  echo "[$(date -Is)] Already at $LOCAL"
  if ! curl -fsS "$HEALTH_URL" >/dev/null 2>&1; then
    echo "[$(date -Is)] App unhealthy; restarting stack"
    docker compose up -d --remove-orphans
  fi
fi

healthy=0
for i in $(seq 1 30); do
  if curl -fsS "$HEALTH_URL" >/tmp/saspro-health.json 2>/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done

if [ "$healthy" -ne 1 ]; then
  echo "[$(date -Is)] Health check failed"
  docker compose ps || true
  docker compose logs --tail=120 saspro || true
  exit 1
fi

echo "[$(date -Is)] SAS PRO is healthy at $(git rev-parse --short HEAD)"
EOF

chmod 755 "$SYNC"

cat > "$SERVICE" <<'EOF'
[Unit]
Description=SAS PRO GitHub synchronization and deployment
After=docker.service
Requires=docker.service

[Service]
Type=oneshot
ExecStart=/usr/local/bin/saspro-github-sync
User=root
Group=root
EOF

cat > "$TIMER" <<'EOF'
[Unit]
Description=Check GitHub for SAS PRO updates every minute

[Timer]
OnBootSec=30s
OnUnitActiveSec=60s
AccuracySec=5s
Persistent=true

[Install]
WantedBy=timers.target
EOF

systemctl daemon-reload
systemctl enable --now saspro-github-sync.timer

# First synchronization now.
systemctl start saspro-github-sync.service

echo
echo "=== SAS PRO GitHub sync installed ==="
systemctl status saspro-github-sync.timer --no-pager || true
echo
echo "Next checks:"
echo "  systemctl list-timers saspro-github-sync.timer"
echo "  journalctl -u saspro-github-sync.service -n 100 --no-pager"
