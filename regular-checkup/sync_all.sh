#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "=== Regular Checkup ==="
echo ""

echo "--- 1. Sync depuis les API providers ---"
python3 regular-checkup/sync_from_providers.py 2>&1 | sed 's/^/  /'

echo ""
echo "--- 2. Sync depuis awesome-free-llm-apis ---"
python3 regular-checkup/sync_from_awesome_list.py 2>&1 | sed 's/^/  /'

echo ""
echo "--- 3. Capacités des modèles ---"
python3 regular-checkup/sync_capabilities.py 2>&1 | sed 's/^/  /'

echo ""
echo "--- 4. Health check ---"
python3 regular-checkup/health_check.py 2>&1 | sed 's/^/  /'

echo ""
echo "=== Terminé ==="
