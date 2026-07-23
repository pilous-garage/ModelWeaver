#!/usr/bin/env bash
# Orchestrateur robuste : lance daemon + Vite + test export YAML.
# Tue tout process existant sur les ports avant de démarrer.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; }

DAEMON_PORT=8770

cleanup() {
    log "Nettoyage…"
    kill $DAEMON_PID $VITE_PID 2>/dev/null || true
    wait $DAEMON_PID $VITE_PID 2>/dev/null || true
    log "Fini"
}
trap cleanup EXIT INT TERM

# ── 0. Kill tout process existant sur les ports ──
log "🔪 Nettoyage des ports…"
fuser -k "$DAEMON_PORT/tcp" 2>/dev/null || true
fuser -k "5173/tcp" 2>/dev/null || true
sleep 1
ok "Ports nettoyés"

# ── 1. Daemon ──
log "Démarrage du daemon sur le port $DAEMON_PORT…"
python3 services/api/daemon.py --port $DAEMON_PORT &
DAEMON_PID=$!
for i in $(seq 1 30); do
    if curl -sf http://127.0.0.1:$DAEMON_PORT/health >/dev/null 2>&1; then
        ok "Daemon OK (pid $DAEMON_PID)"
        break
    fi
    sleep 1
done
if ! kill -0 $DAEMON_PID 2>/dev/null; then
    fail "Daemon non démarré"
    exit 1
fi

# ── 2. Vite ──
log "Démarrage de Vite…"
cd interfaces/main/GUI/official/gui
nohup npm run dev > /tmp/vite_test.log 2>&1 &
VITE_PID=$!
cd "$ROOT"
for i in $(seq 1 30); do
    if curl -sf http://localhost:5173 >/dev/null 2>&1; then
        ok "Vite OK (pid $VITE_PID)"
        break
    fi
    sleep 1
done
if ! kill -0 $VITE_PID 2>/dev/null; then
    fail "Vite non démarré"
    exit 1
fi

# ── 3. Test robuste ──
log "Lancement du test d'export YAML (robuste, timeout progressif 1→10min)…"
PYTHONPATH="$ROOT" python3 "$ROOT/tests/test_graph_export.py" \
    --url http://localhost:5173 \
    --port $DAEMON_PORT \
    --skip-daemon-kill
exit $?
