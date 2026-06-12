#!/bin/bash
# Run on VPS: bash deploy/verify-partner-deploy.sh
# After unzip you MUST restart uvicorn before the API checks pass.
set -euo pipefail
APP="${1:-$HOME/apps/aslan}"
cd "$APP"

echo "=== Disk files ==="
DISK_VER=$(grep -m1 '_PARTNER_UI_VERSION' app/main.py | sed 's/.*"\([^"]*\)".*/\1/')
echo "  main.py partner_ui_version: ${DISK_VER}"
grep -q "portal-topbar" partner/admin.html && echo "  admin.html layout: OK (portal-topbar)" || { echo "  admin.html layout: OLD — re-upload zip and unzip in ~/apps/aslan"; exit 1; }
grep -q "auth-gate" partner/admin.html && echo "  admin.html auth-gate: OK" || { echo "  admin.html auth-gate: MISSING"; exit 1; }
grep -q "symbol-irisid-color-m.png" partner/admin.html && echo "  admin.html symbol: OK" || { echo "  admin.html symbol: MISSING"; exit 1; }
test -f partner/symbol-irisid-color-m.png && echo "  symbol-irisid-color-m.png: OK" || { echo "  symbol-irisid-color-m.png: MISSING on disk"; exit 1; }
grep -q "portal-topbar" partner/register.html && echo "  register.html layout: OK" || { echo "  register.html layout: MISSING"; exit 1; }

echo "=== API process (127.0.0.1:8010) ==="
if ! curl -sf "http://127.0.0.1:8010/health" >/dev/null 2>&1; then
  echo "  uvicorn: NOT RUNNING — start it:"
  echo "    cd ~/apps/aslan && nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 >> ~/apps/aslan-api.log 2>&1 &"
  exit 1
fi

CFG=$(curl -sf "http://127.0.0.1:8010/health/config")
API_VER=$(echo "$CFG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('partner_ui_version',''))")
MARKER=$(echo "$CFG" | python3 -c "import sys,json; print(json.load(sys.stdin).get('partner_admin_marker',''))")
echo "  running partner_ui_version: ${API_VER}"
echo "  partner_admin_marker: ${MARKER}"

if [ "$API_VER" != "$DISK_VER" ]; then
  echo ""
  echo "  *** MISMATCH: API is still old code. Restart required: ***"
  echo "    pkill -f 'uvicorn app.main:app'"
  echo "    cd ~/apps/aslan"
  echo "    nohup .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8010 >> ~/apps/aslan-api.log 2>&1 &"
  echo "    sleep 2 && bash deploy/verify-partner-deploy.sh"
  exit 1
fi

if [ "$MARKER" != "v16_admin_portal_layout" ]; then
  echo ""
  echo "  *** admin.html on disk is not v16 layout (marker=${MARKER}) ***"
  echo "    cd ~/apps/aslan && unzip -o ~/apps/aslan-deploy.zip"
  exit 1
fi

ADMIN_URL="/partner/manage?v=${API_VER}"
CODE=$(curl -sL -o /tmp/aslan-partner-check.html -w "%{http_code}" "http://127.0.0.1:8010${ADMIN_URL}")
if [ "$CODE" != "200" ]; then
  echo "  served admin HTML: HTTP ${CODE} (expected 200 at ${ADMIN_URL})"
  exit 1
fi
echo "  served admin HTML: OK (${ADMIN_URL})"

if grep -q "portal-topbar" /tmp/aslan-partner-check.html && grep -q "auth-gate" /tmp/aslan-partner-check.html; then
  echo "  served admin layout: OK (portal-topbar + auth-gate)"
else
  echo "  served admin layout: OLD HTML — use ?v= URL in browser"
  exit 1
fi

if grep -q "partner/favicon.png?v=" /tmp/aslan-partner-check.html; then
  echo "  served HTML favicon: OK"
else
  echo "  served HTML favicon: MISSING"
  exit 1
fi

if grep -q "href=\"/partner/signup?v=${API_VER}\"" /tmp/aslan-partner-check.html; then
  echo "  served nav links: OK (?v= cache bust)"
else
  echo "  served nav links: MISSING ?v= — check main.py _partner_url"
  exit 1
fi

CODE_FAV=$(curl -sL -o /dev/null -w "%{http_code}" "http://127.0.0.1:8010/partner/favicon.png?v=${API_VER}")
echo "  favicon.png HTTP: ${CODE_FAV}"
if [ "$CODE_FAV" != "200" ]; then
  echo "  favicon.png file: MISSING on server"
  exit 1
fi

rm -f /tmp/aslan-partner-check.html
echo ""
echo "=== All checks passed ==="
echo "Open in browser (always use ?v=):"
echo "  https://chat-api.irisid.com/partner/manage?v=${API_VER}"
echo "  https://chat-api.irisid.com/partner/signup?v=${API_VER}"
