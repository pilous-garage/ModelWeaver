#!/usr/bin/env bash
# Lance Tauri en dev avec gestion automatique du daemon.
# Tue tout process existant sur le port 8770 avant de lancer Tauri.
set -euo pipefail

dir="$(cd "$(dirname "$0")" && pwd)"
cd "$dir"

DAEMON_PORT=8770

echo "🔍 Vérification du port $DAEMON_PORT…"
if fuser "$DAEMON_PORT/tcp" 2>/dev/null; then
    echo "   ⚠️  Port $DAEMON_PORT occupé → kill du process…"
    fuser -k "$DAEMON_PORT/tcp" 2>/dev/null || true
    sleep 1
    if fuser "$DAEMON_PORT/tcp" 2>/dev/null; then
        echo "   ❌ Impossible de libérer le port $DAEMON_PORT"
        exit 1
    fi
    echo "   ✅ Port $DAEMON_PORT libéré"
else
    echo "   ✅ Port $DAEMON_PORT libre"
fi

echo "🚀 Lancement de Tauri dev…"
exec npm run tauri dev
