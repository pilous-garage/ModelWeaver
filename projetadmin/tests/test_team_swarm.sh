#!/usr/bin/env bash
# Test swarm complet via API HTTP directe (pas d'API opencode).
# Utilise le daemon en cours d'exécution sur http://127.0.0.1:8770.
#
# Usage :
#   ./projetadmin/tests/test_team_swarm.sh          # daemon en cours
#   ./projetadmin/tests/test_team_swarm.sh --start   # lance le daemon d'abord

set -euo pipefail

# Auto-découverte du port/token depuis ~/.modelweaver (écrit par le daemon)
MW_HOME="${MODELWEAVER_HOME:-$HOME/.modelweaver}"
PORT=8770
TOKEN=""
read_config() {
  if [ -f "${MW_HOME}/api.port" ]; then
    PORT=$(cat "${MW_HOME}/api.port")
  fi
  if [ -f "${MW_HOME}/api.token" ]; then
    TOKEN=$(cat "${MW_HOME}/api.token")
  fi
  DAEMON_URL="http://127.0.0.1:${PORT}"
  API="${DAEMON_URL}/v1"
  AUTH="${TOKEN:+Authorization: Bearer ${TOKEN}}"
}

read_config

# Les routes d'équipe sont préfixées par team: (ex: team/team:bug-busters/start)
TEAM_ROUTE="team/team:example-team"
BUG_ROUTE="team/team:bug-busters"
PASS=0
FAIL=0

log()  { echo "[$(date +%H:%M:%S)] $*"; }
ok()   { echo "  ✅ $1"; ((PASS++)); }
fail() { echo "  ❌ $1"; ((FAIL++)); }
curlauth() {
  local auth_flag=()
  if [ -n "${TOKEN:-}" ]; then
    auth_flag=(-H "Authorization: Bearer ${TOKEN}")
  fi
  curl -sf "${auth_flag[@]}" "$@"
}

wait_for_daemon() {
  for i in $(seq 1 60); do
    read_config
    # /health ne nécessite pas de token
    if curl -sf "http://127.0.0.1:${PORT}/health" > /dev/null 2>&1; then
      read_config
      return 0
    fi
    sleep 2
  done
  echo "❌ Daemon pas joignable après 120s"
  exit 1
}

# ── Présence ──
log "=== Test Swarm ==="
echo "Cible: ${API}"

if [ "${1:-}" = "--start" ]; then
  log "Lancement du daemon…"
  cd "$(dirname "$0")/../.."
  python3 services/api/daemon.py --port "${PORT}" &
  DAEMON_PID=$!
  trap "kill $DAEMON_PID 2>/dev/null || true" EXIT
  wait_for_daemon
fi

wait_for_daemon
log "Daemon OK"

# ── 1. Vérifier la santé ──
HEALTH=$(curlauth "${API}/system/info")
if echo "$HEALTH" | grep -q '"ok": true'; then
  ok "system/info accessible"
else
  fail "system/info retourne une réponse inattendue: $HEALTH"
fi

# ── 2. charger une équipe manuellement via le daemon ──
# L'équipe "example-team" est chargée au boot via _ensure_teams().
# Vérifier qu'elle existe :
TEAMS=$(curlauth "${API}/${TEAM_ROUTE}/status" 2>&1 || true)
if echo "$TEAMS" | grep -q "example-team"; then
  ok "team example-team chargée"
else
  fail "team example-team absente ou inaccessible"
  echo "  response: $TEAMS"
  exit 1
fi

# ── 3. Démarrer l'équipe ──
START=$(curlauth -X POST "${API}/${TEAM_ROUTE}/start")
if echo "$START" | grep -q "started"; then
  ok "team example-team démarrée ($START)"
else
  fail "échec start team"
  echo "  response: $START"
fi

# ── 4. Vérifier les agents ──
AGENTS=$(curlauth "${API}/${TEAM_ROUTE}/status")
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
DELEGATE=$(curlauth -X POST "${API}/${TEAM_ROUTE}/delegate" \
  -H 'Content-Type: application/json' \
  -d '{"request": "list 3 programming languages and their primary use cases"}')
if echo "$DELEGATE" | grep -q "ok"; then
  ok "délégation acceptée"
else
  fail "échec délégation: $DELEGATE"
fi

# ── 6. Chat direct avec le team_leader ──
CHAT=$(curlauth -X POST "${API}/${TEAM_ROUTE}/chat" \
  -H 'Content-Type: application/json' \
  -d '{"message": "what team are you?"}')
if echo "$CHAT" | grep -q "ok"; then
  ok "chat team_leader OK"
else
  fail "échec chat: $CHAT"
fi

# ── 7. Vérifier la supervision (tick) ──
TICK=$(curlauth -X POST "${API}/tick")
if echo "$TICK" | grep -q "ok"; then
  ok "tick supervision OK"
else
  fail "tick échoué: $TICK"
fi

# ── 8. Arrêter l'équipe ──
STOP=$(curlauth -X POST "${API}/${TEAM_ROUTE}/stop")
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
