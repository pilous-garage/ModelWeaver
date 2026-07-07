#!/bin/bash
set -euo pipefail

echo "🧹 Arrêt des containers modelweaver..."
docker kill $(docker ps -q --filter "label=modelweaver") 2>/dev/null || true

echo "🗑️  Suppression des containers modelweaver..."
docker rm $(docker ps -aq --filter "label=modelweaver") 2>/dev/null || true

echo "📦 Suppression des images modelweaver-..."
docker rmi $(docker images 'modelweaver-*' -q) 2>/dev/null || true

echo "📦 Suppression des images modelweaver-bak:* (snapshots temporaires)..."
docker rmi $(docker images 'modelweaver-bak:*' -q) 2>/dev/null || true

echo "✅ Nettoyage terminé. (Les backups .tar dans docker-backup/ sont conservés)"
