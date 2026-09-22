#!/usr/bin/env bash
set -euo pipefail

cd /opt/saspro

echo "==> Updating SAS PRO"
git pull --ff-only origin main

echo "==> Validating Compose"
docker compose config >/dev/null

echo "==> Building SAS PRO + PanWatch + OpenTerminal"
docker compose build --no-cache

echo "==> Starting services"
docker compose up -d

echo "==> Service status"
docker compose ps

echo "==> SAS PRO health"
curl -fsS http://127.0.0.1:8000/health
echo

echo "==> OpenTerminal web"
curl -fsSI http://127.0.0.1:3001 | head -n 1

echo
echo "Deployment completed."
