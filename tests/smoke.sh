#!/usr/bin/env bash
# Smoke auto : lance le daemon + Vite + smoke test Playwright.
# Usage: bash tests/smoke.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT=120

cleanup() {
    kill $DAEMON_PID $VITE_PID 2>/dev/null || true
    wait $DAEMON_PID $VITE_PID 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# ── 1. Daemon backend ──
echo "🚧 Daemon 8771..."
kill -9 $(lsof -ti tcp:8771 2>/dev/null) 2>/dev/null || true
sleep 1
cd "$ROOT"
python3 services/api/daemon.py --port 8771 > /tmp/daemon_smoke.log 2>&1 &
DAEMON_PID=$!
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:8771/health >/dev/null 2>&1; then
        echo "✅ Daemon OK (pid $DAEMON_PID)"
        break
    fi
    sleep 1
done
if ! kill -0 $DAEMON_PID 2>/dev/null; then
    echo "❌ Daemon non démarré"
    cat /tmp/daemon_smoke.log
    exit 1
fi

# ── 2. Vite ──
echo "🚀 npm run dev (timeout=${TIMEOUT}s)"
cd "$ROOT/interfaces/main/GUI/official/gui"
npm run dev &
VITE_PID=$!
for i in $(seq 1 30); do
    if curl -sf http://localhost:5173 >/dev/null 2>&1; then
        echo "✅ Vite OK (pid $VITE_PID)"
        break
    fi
    sleep 1
done

# Keep remaining code unchanged below
# Smoke via Playwright (headless)
echo "🌐 Smoke Playwright extract..."
PYTHONPATH="$ROOT" python3 << 'PYEOF'
import time, json
from playwright.sync_api import sync_playwright

p = sync_playwright().start()
b = p.chromium.launch(headless=True)
ctx = b.new_context(viewport={"width": 1400, "height": 900})
page = ctx.new_page()
page.set_default_timeout(90000)

page.goto("http://localhost:5173/?smoke=1", wait_until="domcontentloaded")
print("   page chargée, URL=" + page.url)

start = time.time()
while time.time() - start < 30:
    result = page.evaluate("window.__smoke_result")
    if result is not None:
        print(f"   result reçu après {time.time()-start:.1f}s")
        break
    time.sleep(0.5)
else:
    print("⚠️  __smoke_result jamais défini")
    # dump console logs
    logs = page.evaluate("() => { try { return window.__smoke_console || []; } catch(e) { return []; } }")
    print("   console logs:", logs)
    result = None

if result and not isinstance(result, dict):
    result = {"value": str(result)}
if result:
    print(json.dumps(result, indent=2))
else:
    print("ÉCHEC : aucun résultat smoke")

yaml_data = page.evaluate("window.__smoke_yaml || null")
if yaml_data:
    from pathlib import Path
    Path("/tmp/smoke_worker.yaml").write_text(yaml_data, encoding="utf-8")
    print(f"📄 YAML → /tmp/smoke_worker.yaml ({len(yaml_data)} chars)")

b.close()
p.stop()
PYEOF

exit $?