#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CONTAINER_NAME="mw-codeur-$(date +%s)"
IMAGE_NAME="modelweaver-codeur-test"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Test Agent Codeur — Docker"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# Build de l'image de base si nécessaire
if ! docker image inspect ubuntu-bare &>/dev/null; then
    echo "📦 Construction de l'image de base ubuntu-bare..."
    docker build -t ubuntu-bare -f- "$PROJECT_DIR" <<'DOCKERFILE'
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
    ubuntu-bare \
    sleep 9999 >/dev/null

cleanup() {
    echo "🧹 Nettoyage..."
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

# Copie du projet
echo "📂 Copie du projet..."
tar cf - \
    --exclude='.opencode' \
    --exclude='.modelweaver' \
    --exclude='__pycache__' \
    --exclude='.venv' \
    --exclude='.git' \
    --exclude='docker-backup' \
    -C "$PROJECT_DIR" . | docker cp - "$CONTAINER_NAME:/app"

# Copie du .env
if [ -f "$PROJECT_DIR/.env" ]; then
    echo "🔑 Copie de .env..."
    docker cp "$PROJECT_DIR/.env" "$CONTAINER_NAME:/app/.env"
fi

# Démarrer le container
docker start "$CONTAINER_NAME" >/dev/null

# Créer le répertoire .modelweaver pour que la BDD puisse être créée
docker exec "$CONTAINER_NAME" mkdir -p /app/.modelweaver

# Lancer le test
echo "🧪 Lancement du test..."
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
EXIT_CODE=0
docker exec "$CONTAINER_NAME" python3 /app/tests/test_agent_codeur.py || EXIT_CODE=$?

# Récupérer le script généré
if docker exec "$CONTAINER_NAME" test -f /app/tests/tic_tac_toe_output.py 2>/dev/null; then
    echo "📥 Récupération du script généré..."
    docker cp "$CONTAINER_NAME:/app/tests/tic_tac_toe_output.py" "$SCRIPT_DIR/tic_tac_toe_docker_output.py"
fi

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

if [ $EXIT_CODE -eq 0 ]; then
    echo "✅ Test Agent Codeur Docker PASSÉ"
else
    echo "❌ Test Agent Codeur Docker ÉCHOUÉ (code $EXIT_CODE)"
fi

exit $EXIT_CODE
