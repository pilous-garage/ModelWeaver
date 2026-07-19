#!/usr/bin/env bash
# tauri-probe.sh — probe court (30s max) du log Tauri, rend la main.
set -u
LOG="/tmp/mw_tauri.log"
for i in $(seq 1 6); do
  if grep -qiE "Finished|panicked|error\[|error:|supervisor.lock|Running .target/debug/modelweaver." "$LOG" 2>/dev/null; then break; fi
  sleep 5
done
echo "=== Tauri log (tail) ==="
tail -12 "$LOG" 2>/dev/null
echo "=== binaire GUI ==="
pgrep -f "target/debug/modelweaver" >/dev/null && echo "ACTIF" || echo "ARRETE"
