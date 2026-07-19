#!/usr/bin/env bash
# launch_gui.sh — lance la GUI Tauri dans une session separee, totalement
# detachee du shell appelant (tous les fd redirects). Rend la main immediatement.
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
GUI="$ROOT/interfaces/main/GUI/official/gui"
LOG="/tmp/mw_tauri.log"
exec setsid bash -c "
  cd '$GUI'
  exec npm run tauri dev >'$LOG' 2>&1 < /dev/null
" >/dev/null 2>&1 </dev/null &
echo "launched pid=$!"
