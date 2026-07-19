#!/usr/bin/env bash
# continue-test.sh — probe le backend lance par start-test.sh.
# Timeout global de 60s : rend la main au shell meme si le backend ne repond pas.
set -u
PORT="${1:-8770}"
TIMEOUT="${2:-60}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"

TOKEN="$(cat "$HOME/.modelweaver/api.token" 2>/dev/null || echo "")"

probe() {
  local route="$1"
  local code
  code=$(curl -s -o /tmp/mw_resp.json -w "%{http_code}" -m 8 -X POST \
    -H "Authorization: Bearer $TOKEN" -d '{}' \
    "http://127.0.0.1:$PORT/v1/$route" 2>/dev/null)
  echo "[$code] $route -> $(head -c 140 /tmp/mw_resp.json 2>/dev/null)"
}

echo "=== Probe backend (timeout global ${TIMEOUT}s) ==="
end=$(( $(date +%s) + TIMEOUT ))
for r in system/info agent/list usage/budget usage/free_tier catalogue/tools/list keys/list llm/models/list; do
  if [ "$(date +%s)" -gt "$end" ]; then echo "TIMEOUT atteint"; break; fi
  probe "$r"
done
echo "=== Daemon log (tail) ==="
tail -4 /tmp/mw_daemon_gui.log 2>/dev/null
echo "=== AFD log (tail) ==="
tail -4 /tmp/mw_afd.log 2>/dev/null
echo "(main rendue)"
