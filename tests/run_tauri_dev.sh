#!/usr/bin/env bash
# Lance `npm run tauri dev` avec timeout, capture stdout/stderr.
# Usage: bash tests/run_tauri_dev.sh [timeout_seconds]
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TIMEOUT="${1:-60}"

cd "$ROOT/interfaces/main/GUI/official/gui"

echo "=== TAURI DEV (timeout=${TIMEOUT}s) ==="
echo "Log file: /tmp/tauri_dev.log"
echo

timeout "$TIMEOUT" npm run tauri dev > /tmp/tauri_dev.log 2>&1
EXIT=$?

echo "--- EXIT CODE: $EXIT ---"
echo "--- LOG (last 60 lines) ---"
tail -60 /tmp/tauri_dev.log
echo "--- LOG END ---"
exit $EXIT