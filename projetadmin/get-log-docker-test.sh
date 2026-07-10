#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONTAINER="modelweaver-test"
OUT="$SCRIPT_DIR/docker-test-log.txt"
GUI_LOG="$SCRIPT_DIR/docker-test-gui.log"

echo "[INFO] Récupération des logs du conteneur $CONTAINER..."

EXISTS=$(docker ps -a --filter name="$CONTAINER" --format "{{.Names}}" 2>/dev/null)
if [ -z "$EXISTS" ]; then
    echo "[ERROR] Conteneur '$CONTAINER' introuvable."
    echo "       Lance d'abord : ./projetadmin/test-gui.sh"
    exit 1
fi

RUNNING=$(docker ps --filter name="$CONTAINER" --format "{{.Names}}" 2>/dev/null)

echo "[INFO] Logs enregistrés dans $OUT"
echo "========================================" > "$OUT"
echo "Logs du conteneur modelweaver-test" >> "$OUT"
echo "Date: $(date)" >> "$OUT"
echo "État: $(docker inspect "$CONTAINER" --format '{{.State.Status}}')" >> "$OUT"
echo "========================================" >> "$OUT"

echo "" >> "$OUT"
echo "--- DOCKER LOGS (stdout/stderr) ---" >> "$OUT"
docker logs "$CONTAINER" >> "$OUT" 2>&1 || true

echo "" >> "$OUT"
echo "--- GUI.LOG (depuis le conteneur) ---" >> "$OUT"
docker cp "$CONTAINER":/root/.modelweaver/gui.log "$GUI_LOG" 2>/dev/null || echo "[WARN] gui.log non trouvé" >> "$OUT"
if [ -f "$GUI_LOG" ]; then
    cat "$GUI_LOG" >> "$OUT"
    echo "[INFO] gui.log copié dans $GUI_LOG"
fi

echo "[INFO] Fichiers générés :"
echo "  - $OUT  (logs complets)"
echo "  - $GUI_LOG  (gui.log brut)"
