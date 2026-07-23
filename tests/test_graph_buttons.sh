#!/usr/bin/env bash
# Test interactif des boutons du graphe (Tout déplier, Log)
# Usage: bash tests/test_graph_buttons.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# ── Couleurs ──
GREEN='\033[0;32m'; CYAN='\033[0;36m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${CYAN}[$(date +%H:%M:%S)]${NC} $*"; }
ok()   { echo -e "${GREEN}  ✓${NC} $*"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $*"; }
fail() { echo -e "${RED}  ✗${NC} $*"; }

cleanup() {
    log "Nettoyage…"
    kill $DAEMON_PID $VITE_PID 2>/dev/null || true
    wait $DAEMON_PID $VITE_PID 2>/dev/null || true
    log "Fini"
}
trap cleanup EXIT INT TERM

# ── 1. Démarrer le daemon ──
DAEMON_PORT=8770
log "Démarrage du daemon sur le port $DAEMON_PORT…"
python3 services/api/daemon.py --port $DAEMON_PORT &
DAEMON_PID=$!
# Attendre que le daemon réponde
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

# ── 2. Démarrer Vite ──
log "Démarrage de Vite…"
cd interfaces/main/GUI/official/gui
npm run dev &
VITE_PID=$!
cd "$ROOT"
# Attendre que Vite écoute
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

# ── 3. Lancer le test Playwright ──
log "Lancement du test Playwright (timeout 300s)…"
PYTHONPATH="$ROOT" python3 "$ROOT/tests/test_gui_expand_all.py" \
    --url http://localhost:5173 \
    --timeout 300
exit $?
