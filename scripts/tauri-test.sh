#!/usr/bin/env bash
# tauri-test.sh — lance la GUI Tauri en arriere-plan et rend la main immediatement.
# La surveillance se fait via: tail -f /tmp/mw_tauri.log
# Usage: ./scripts/tauri-test.sh [port-daemon]
set -u
PORT="${1:-8770}"
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI="$ROOT/interfaces/main/GUI/official/gui"
LOG="/tmp/mw_tauri.log"

cd "$GUI"
pkill -f "tauri dev" 2>/dev/null || true
pkill -f "vite" 2>/dev/null || true
pkill -f "target/debug/modelweaver" 2>/dev/null || true
sleep 1

echo "Lancement Tauri en arriere-plan (log: $LOG)..."
setsid bash -c "npm run tauri dev > $LOG 2>&1" &
echo "PID tauri=$!"
echo "Le shell est libre. Surveillez avec: tail -f $LOG"
echo "Ou relancez un probe court: ./scripts/tauri-probe.sh"
