#!/bin/bash
# Run on VPS after git push from Mac:
#   cd ~/apps/aslan && bash deploy/update-partner-ui.sh
set -euo pipefail
APP="${1:-$HOME/apps/aslan}"
cd "$APP"

echo "=== git pull ==="
git fetch origin main
git checkout -f main
git pull --ff-only origin main

echo "=== restart API ==="
if systemctl is-active --quiet aslan-api 2>/dev/null; then
  sudo systemctl restart aslan-api
  sleep 2
elif pgrep -f "uvicorn app.main:app" >/dev/null; then
  pkill -f "uvicorn app.main:app" || true
  sleep 1
  nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 >> ~/apps/aslan-api.log 2>&1 &
  sleep 2
else
  echo "No running API found — start manually:"
  echo "  cd $APP && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 >> ~/apps/aslan-api.log 2>&1 &"
fi

echo "=== verify ==="
bash deploy/verify-partner-deploy.sh "$APP"
