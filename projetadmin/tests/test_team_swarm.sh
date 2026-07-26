#!/usr/bin/env bash
# Test swarm complet via API HTTP directe (pas d'API opencode).
# Utilise le daemon en cours d'exécution sur http://127.0.0.1:8770.
#
# Usage :
#   ./projetadmin/tests/test_team_swarm.sh          # daemon en cours
#   ./projetadmin/tests/test_team_swarm.sh --start   # lance le daemon d'abord

set -euo pipefail

DAEMON_URL="${DAEMON_URL:-http://127.0.0.1:8770}"
API="${DAEMON_URL}/v1"
TEAM="example-team"
PASS=0
FAIL=0

log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { echo "  ✅ $1"; ((PASS++)); }
fail() { echo "  ❌ $1"; ((FAIL++)); }

wait_for_daemon() {
  for i in $(seq 1 30); do
    if curl -sf "${API}/system/info" > /dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  echo "❌ Daemon pas joignable après 30s"
  exit 1
}

# ── Présence ──
log "=== Test Swarm ==="
echo "Cible: ${API}"

if [ "${1:-}" = "--start" ]; then
  log "Lancement du daemon…"
  cd "$(dirname "$0")/../.."
  python3 services/api/daemon.py --port 8770 &
  DAEMON_PID=$!
  trap "kill $DAEMON_PID 2>/dev/null || true" EXIT
  wait_for_daemon
fi

wait_for_daemon
log "Daemon OK"

# ── 1. Vérifier la santé ──
HEALTH=$(curl -sf "${API}/system/info")
if echo "$HEALTH" | grep -q "version"; then
  ok "system/info accessible"
else
  fail "system/info retourne une réponse inattendue"
fi

# ── 2. charger une équipe manuellement via le daemon ──
# L'équipe "example-team" est chargée au boot via _ensure_teams().
# Vérifier qu'elle existe :
TEAMS=$(curl -sf "${API}/team/example-team/status" 2>&1 || true)
if echo "$TEAMS" | grep -q "example-team"; then
  ok "team example-team chargée"
else
  fail "team example-team absente ou inaccessible"
  echo "  response: $TEAMS"
  exit 1
fi

# ── 3. Démarrer l'équipe ──
START=$(curl -sf -X POST "${API}/team/example-team/start")
if echo "$START" | grep -q "started"; then
  ok "team example-team démarrée ($START)"
else
  fail "échec start team"
  echo "  response: $START"
fi

# ── 4. Vérifier les agents ──
AGENTS=$(curl -sf "${API}/team/example-team/status")
WORKER_A=$(echo "$AGENTS" | grep -c "worker-a" || true)
WORKER_B=$(echo "$AGENTS" | grep -c "worker-b" || true)
if [ "$WORKER_A" -gt 0 ] && [ "$WORKER_B" -gt 0 ]; then
  ok "membres workers présents dans l'équipe"
else
  fail "workers manquants: $AGENTS"
fi

LEADER=$(echo "$AGENTS" | grep -c "team_leader" || true)
if [ "$LEADER" -gt 0 ]; then
  ok "team_leader présent dans l'équipe"
else
  fail "team_leader manquant: $AGENTS"
fi

# ── 5. Déléguer une tâche au team_leader ──
log "=== Délégation ==="
DELEGATE=$(curl -sf -X POST "${API}/team/example-team/delegate" \
  -H 'Content-Type: application/json' \
  -d '{"request": "list 3 programming languages and their primary use cases"}')
if echo "$DELEGATE" | grep -q "ok"; then
  ok "délégation acceptée"
else
  fail "échec délégation: $DELEGATE"
fi

# ── 6. Chat direct avec le team_leader ──
CHAT=$(curl -sf -X POST "${API}/team/example-team/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message": "what team are you?"}')
if echo "$CHAT" | grep -q "ok"; then
  ok "chat team_leader OK"
else
  fail "échec chat: $CHAT"
fi

# ── 7. Vérifier la supervision (tick) ──
TICK=$(curl -sf -X POST "${API}/tick")
if echo "$TICK" | grep -q "ok"; then
  ok "tick supervision OK"
else
  fail "tick échoué: $TICK"
fi

# ── 8. Arrêter l'équipe ──
STOP=$(curl -sf -X POST "${API}/team/example-team/stop")
if echo "$STOP" | grep -q "stopped"; then
  ok "team example-team arrêtée"
else
  fail "échec stop: $STOP"
fi

# ── Bilan ──
echo "═══════════════════════════"
echo "Résultats: ${PASS} ✅ / ${FAIL} ❌ / $((PASS+FAIL)) total"
echo "═══════════════════════════"
[ "$FAIL" -eq 0 ] || exit 1
