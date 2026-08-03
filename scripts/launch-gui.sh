#!/usr/bin/env bash
# launch-gui.sh — Lance le GUI Tauri avec logs maximisés
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/interfaces/main/GUI/official/gui"

LOG_DIR="$HOME/.modelweaver/logs"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date +%Y%m%d-%H%M%S)

echo "=== ModelWeaver GUI Launch ==="
echo "Timestamp: $TIMESTAMP"
echo "Logs: $LOG_DIR/gui-$TIMESTAMP.log"

# 1. Tuer les anciennes instances
echo ">>> Killing old instances..."
pkill -f "modelweaver" 2>/dev/null || true
pkill -f "daemon.py" 2>/dev/null || true
sleep 1

# 2. Supprimer la runtime DB pour éviter les conflits
echo ">>> Removing runtime.db..."
rm -f "$HOME/.modelweaver/runtime.db"

# 3. Démarrer le daemon Python séparément (pour voir ses logs)
echo ">>> Starting daemon..."
python3 "$ROOT/services/api/daemon.py" > "$LOG_DIR/daemon-$TIMESTAMP.log" 2>&1 &
DAEMON_PID=$!
echo "    Daemon PID: $DAEMON_PID"

# 4. Attendre que le daemon soit prêt
echo ">>> Waiting for daemon..."
for i in $(seq 1 15); do
    if curl -s http://127.0.0.1:8770/v1/version -o /dev/null 2>/dev/null; then
        echo "    Daemon ready after ${i}s"
        break
    fi
    sleep 1
done

# 5. Tester les routes daemon
echo ">>> Testing daemon routes..."
curl -s http://127.0.0.1:8770/v1/version | head -c 200
echo ""
curl -s -X POST http://127.0.0.1:8770/v1/layout/get -d '{"name":"default"}' | head -c 200
echo ""
curl -s -X POST http://127.0.0.1:8770/v1/layout/get -d '{"name":"dashboard"}' | head -c 200
echo ""
curl -s -X POST http://127.0.0.1:8770/v1/layout/get -d '{"name":"agentIde"}' | head -c 200
echo ""

# 6. Lancer le GUI Tauri
echo ">>> Starting Tauri GUI..."
echo "    Logs: $LOG_DIR/gui-$TIMESTAMP.log"
npm run tauri dev > "$LOG_DIR/gui-$TIMESTAMP.log" 2>&1 &
GUI_PID=$!
echo "    GUI PID: $GUI_PID"

# 7. Attendre et vérifier
echo ">>> Waiting 60s for GUI to stabilize..."
sleep 60

# 8. Vérifier les logs du GUI
echo ""
echo "=== Last 30 lines of GUI log ==="
tail -30 "$LOG_DIR/gui-$TIMESTAMP.log"

echo ""
echo "=== Daemon status ==="
curl -s http://127.0.0.1:8770/v1/version 2>/dev/null || echo "Daemon not responding"

echo ""
echo "=== Done ==="
echo "Daemon PID: $DAEMON_PID"
echo "GUI PID: $GUI_PID"
echo "To stop: pkill -f modelweaver; pkill -f daemon.py"
