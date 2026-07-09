#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="mw-autodebug-$(date +%s)"
IMAGE_NAME="modelweaver-autodebug-test"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Auto-Debug E2E — Docker"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Build de l'image
if ! docker image inspect $IMAGE_NAME &>/dev/null; then
    echo "📦 Construction de l'image..."
    docker build -t $IMAGE_NAME -f- "$PROJECT_DIR" <<'DOCKERFILE'
FROM ubuntu:24.04
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    python3 python3-pip python3-yaml python3-requests \
    git curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
DOCKERFILE
fi

# Créer le container
echo "🚀 Création du container..."
docker create \
    --workdir /app \
    --name "$CONTAINER_NAME" \
    --env PYTHONUNBUFFERED=1 \
    $IMAGE_NAME \
    sleep 9999 >/dev/null

cleanup() {
    echo "🧹 Nettoyage..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# Copie du projet
echo "📂 Copie du projet..."
tar cf - \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='.modelweaver/cache' \
    --exclude='.modelweaver/apt-lists' \
    --exclude='.modelweaver/apt-cache' \
    --exclude='.opencode' \
    -C "$PROJECT_DIR" . | docker cp - "$CONTAINER_NAME:/app"

# Copie du .env
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "🔑 Copie de .env..."
    docker cp "$PROJECT_DIR/.env" "$CONTAINER_NAME:/app/.env"
fi

# Démarrer le container
docker start "$CONTAINER_NAME" >/dev/null

# Setup BDD
docker exec "$CONTAINER_NAME" mkdir -p /app/.modelweaver
docker exec "$CONTAINER_NAME" rm -f /app/.modelweaver/modelweaver.db

# Lancer le test E2E
echo "🧪 Lancement de la boucle Auto-Debug..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
EXIT_CODE=0
docker exec -e PYTHONPATH=/app "$CONTAINER_NAME" python3 /app/tests/test_autodebug_e2e.py || EXIT_CODE=$?

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test Auto-Debug PASSÉ"
else
    echo "❌ Test Auto-Debug ÉCHOUÉ (code $EXIT_CODE)"
fi

exit $EXIT_CODE
