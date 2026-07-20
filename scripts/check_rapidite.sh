#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <timeout_seconds>"
    echo "  Build release, lance la GUI, attend <timeout>s, affiche les TIMING."
    exit 1
fi

TIMEOUT=$1
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
GUI_DIR="$REPO_DIR/interfaces/main/GUI/official/gui"
BINARY="$GUI_DIR/src-tauri/target/release/modelweaver"
GUI_LOG="$HOME/.modelweaver/gui.log"

echo "=== Build release ==="
cd "$GUI_DIR"
npm run tauri build 2>&1 | tail -5

echo "=== Kill existing ==="
pkill -f "target/release/modelweaver" 2>/dev/null || true
pkill -f "daemon.py serve" 2>/dev/null || true
sleep 2

echo "=== Launch (timeout=${TIMEOUT}s) ==="
setsid "$BINARY" </dev/null >/dev/null 2>&1 &
BGPID=$!
echo "pid=$BGPID"

# Attendre que le daemon api soit prêt (port 8770)
for i in $(seq 1 30); do
    if curl -s -o /dev/null -w "" --max-time 1 http://127.0.0.1:8770/health 2>/dev/null; then
        echo "daemon pret apres ${i}s"
        break
    fi
    sleep 1
done

echo "=== Attente ${TIMEOUT}s ==="
sleep "$TIMEOUT"

echo "=== Kill ==="
kill -- -$(ps -o pgid= -p "$BGPID" 2>/dev/null | tr -d ' ') 2>/dev/null || true
pkill -f "target/release/modelweaver" 2>/dev/null || true
pkill -f "daemon.py serve" 2>/dev/null || true
sleep 1

echo ""
echo "=== TIMING ==="
if [ -f "$GUI_LOG" ]; then
    grep -E "^\[[0-9]+\] \[TIMING\]" "$GUI_LOG" || echo "(aucune entrée TIMING)"
else
    echo "(log introuvable: $GUI_LOG)"
fi
