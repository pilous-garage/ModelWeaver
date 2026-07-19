#!/bin/bash
# Moniteur du test scénario échecs.
#   - Affiche un instantané de l'état (status, dernières lignes du log,
#     santé daemon, process test/daemon/AFD).
#   - Dort 120s (pour laisser le test avancer) PUIS rend la main.
#   - Sort immédiatement si le test est déjà terminé (status DONE).
set -u

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT="${MW_TEST_PORT:-8771}"
LOG="/tmp/chess_test.log"
STATUS="/tmp/chess_test.status"
SLEEP="${MW_MONITOR_SLEEP:-120}"

echo "======================================================================"
echo " MONITEUR TEST ÉCHECS — $(date '+%H:%M:%S')"
echo "======================================================================"

# ── État global ──
ST="$(cat "$STATUS" 2>/dev/null || echo INCONNU)"
echo "[status] $ST"

# ── Santé daemon ──
H=$(curl -s -m 2 "http://127.0.0.1:${PORT}/health" 2>/dev/null || echo "DOWN")
echo "[daemon:${PORT}] $H"

# ── Process ──
ME=$$
NTEST=$(pgrep -f 'test_scenario_chess.py' | grep -vc "$ME" 2>/dev/null || echo 0)
NAFD=$(pgrep -cf 'python3 services/afd/service.py' 2>/dev/null || echo 0)
echo "[procs] test=$NTEST  afd=$NAFD"

# ── Résumé PASS/FAIL du log ──
if [ -f "$LOG" ]; then
  NPASS=$(grep -c '\[PASS\]' "$LOG" 2>/dev/null || echo 0)
  NFAIL=$(grep -c '\[FAIL\]' "$LOG" 2>/dev/null || echo 0)
  echo "[log] PASS=$NPASS FAIL=$NFAIL"
  echo "---------------------------- dernières lignes ----------------------------"
  tail -25 "$LOG"
  echo "-------------------------------------------------------------------------"
else
  echo "[log] $LOG introuvable"
fi

# ── Décision : rendre la main si terminé ──
if [ "$ST" = "DONE" ]; then
  echo "[monitor] test TERMINÉ — sortie immédiate."
  RESULT_LINE=$(grep -E 'RESULT:' "$LOG" 2>/dev/null | tail -1)
  EXIT_LINE=$(grep -E 'EXIT_CODE=' "$LOG" 2>/dev/null | tail -1)
  echo "[monitor] $RESULT_LINE"
  echo "[monitor] $EXIT_LINE"
  exit 0
fi

echo "[monitor] test EN COURS — sleep ${SLEEP}s puis rend la main."
sleep "$SLEEP"
echo "[monitor] réveil $(date '+%H:%M:%S') — relancer le moniteur pour la suite."
exit 0
