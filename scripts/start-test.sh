#!/usr/bin/env bash
# start-test.sh — lance le backend ModelWeaver (AFD + daemon + collector)
# en arrière-plan, avec reset propre des logs. Ne bloque pas le shell.
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
set -a; [ -f .env ] && . ./.env; set +a

PORT="${1:-8770}"
AFD_LOG="/tmp/mw_afd.log"
DAEMON_LOG="/tmp/mw_daemon_gui.log"

# Nettoyage des processus existants
pkill -f "services/api/daemon.py serve" 2>/dev/null || true
pkill -f "services/afd/service.py serve" 2>/dev/null || true
pkill -f "modules/usage/usage_collector.py" 2>/dev/null || true
sleep 1

nohup python3 services/afd/service.py serve --poll 2 >"$AFD_LOG" 2>&1 &
nohup python3 services/api/daemon.py serve --port "$PORT" >"$DAEMON_LOG" 2>&1 &

echo "Backend lance (AFD + daemon:$PORT) en arrière-plan."
echo "Logs: $AFD_LOG / $DAEMON_LOG"
echo "Lancez ensuite: ./scripts/continue-test.sh $PORT"
