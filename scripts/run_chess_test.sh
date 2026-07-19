#!/bin/bash
# Lanceur DÉTACHÉ du test scénario échecs.
#   - Tue le daemon 8771 + les AFD (restart propre à chaque run).
#   - Relance AFD + daemon 8771 avec le code courant.
#   - Lance tests/test_scenario_chess.py en arrière-plan (setsid, timeout dur).
#   - Rend la main IMMÉDIATEMENT. Suivre l'avancement via monitor_chess_test.sh.
#
# L'interaction avec le programme pendant le test passe UNIQUEMENT par le
# daemon (MWClient) — test en conditions réelles.
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT" || exit 1

PORT="${MW_TEST_PORT:-8771}"
HARD_TIMEOUT="${MW_TEST_TIMEOUT:-600}"          # timeout dur du test (s)
LOG="/tmp/chess_test.log"
STATUS="/tmp/chess_test.status"                 # RUNNING | DONE
DAEMON_LOG="/tmp/daemon${PORT}.log"
AFD_LOG="/tmp/afd.log"

echo "[run] racine=$ROOT port=$PORT timeout=${HARD_TIMEOUT}s"

# ── 1. Kill daemon 8771 + AFD (sans se tuer soi-même) ──
ME=$$
for p in $(pgrep -f "daemon.py serve --port ${PORT}"); do
  [ "$p" != "$ME" ] && kill -9 "$p" 2>/dev/null
done
for p in $(pgrep -f 'python3 services/afd/service.py'); do
  [ "$p" != "$ME" ] && kill -9 "$p" 2>/dev/null
done
sleep 1
echo "[run] daemon+AFD tués"

# ── 2. Restart AFD + daemon ──
setsid bash -c "cd '$ROOT' && python3 services/afd/service.py serve --poll 2 > '$AFD_LOG' 2>&1" &
setsid bash -c "cd '$ROOT' && python3 services/api/daemon.py serve --port ${PORT} > '$DAEMON_LOG' 2>&1" &

HEALTH=""
for i in $(seq 1 25); do
  HEALTH=$(curl -s -m 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null)
  [ -n "$HEALTH" ] && break
  sleep 1
done
if [ -z "$HEALTH" ]; then
  echo "[run] ERREUR : daemon ${PORT} ne répond pas. Voir $DAEMON_LOG"
  echo "DONE" > "$STATUS"
  exit 1
fi
echo "[run] daemon UP : $HEALTH"

# ── 3. Lancer le test en détaché ──
: > "$LOG"
echo "RUNNING" > "$STATUS"
setsid bash -c "
  cd '$ROOT'
  PYTHONPATH=. timeout ${HARD_TIMEOUT} python3 tests/test_scenario_chess.py > '$LOG' 2>&1
  echo \"EXIT_CODE=\$?\" >> '$LOG'
  echo DONE > '$STATUS'
" &

echo "[run] test lancé en détaché. Log : $LOG  Status : $STATUS"
echo "[run] utiliser scripts/monitor_chess_test.sh pour suivre."
exit 0
