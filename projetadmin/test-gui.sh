#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$SCRIPT_DIR/docker"
MAIN_BIN="$SCRIPT_DIR/gui-main/src-tauri/target/release/modelweaver"
HELPER="$SCRIPT_DIR/gui-main/gui_helper.py"
IMAGE="modelweaver-gui-test"
CONTAINER="modelweaver-gui-test"
LAST_FILE="$SCRIPT_DIR/.last-test-gui-time"

TIMEOUT_DEFAULT=300
TIMESTAMP=$(date +%Y%m%d%H%M%S)
TAG=""

ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# --- Parse arguments ---
for arg in "$@"; do
    case "$arg" in
        --timeout=*) TIMEOUT="${arg#*=}" ;;
    esac
done

# --- Calcul du timeout ---
if [ -z "${TIMEOUT:-}" ]; then
    if [ -f "$LAST_FILE" ]; then
        LAST=$(cat "$LAST_FILE")
        TIMEOUT=$((LAST * 2))
        [ "$TIMEOUT" -lt 60 ] && TIMEOUT=60
        log "Timeout auto: ${LAST}s * 2 = ${TIMEOUT}s"
    else
        TIMEOUT=$TIMEOUT_DEFAULT
        log "Timeout par défaut: ${TIMEOUT}s"
    fi
else
    log "Timeout personnalisé: ${TIMEOUT}s"
fi

START=$(date +%s)
LOG_FILE="$SCRIPT_DIR/log-test-gui-${TIMESTAMP}.log"

{
    echo "ModelWeaver — test-gui.sh"
    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Timeout: ${TIMEOUT}s"
    echo ""
} > "$LOG_FILE"

trap cleanup EXIT
docker rm -f "$CONTAINER" 2>/dev/null || true

cleanup() {
    local ec=$?
    docker rm -f "$CONTAINER" 2>/dev/null || true
    [ -d "$DOCKER_DIR" ] && rm -rf "$DOCKER_DIR"
    xhost -local: >/dev/null 2>&1 || true
    return "$ec"
}

# Rediriger tout stdout vers le log + terminal
exec 5>&1
exec > >(tee -a "$LOG_FILE" >&5)

# --- Vérifications préalables ---
log "Vérification des prérequis..."
[ -f "$MAIN_BIN" ] || { log "✗ Main introuvable: $MAIN_BIN"; log "  → Build : cd gui-main && npm run tauri build"; exit 1; }
[ -f "$HELPER" ] || { log "✗ Helper introuvable: $HELPER"; exit 1; }
log "  ✓ Binaire main: $(ls -lh "$MAIN_BIN" | awk '{print $5}')"
log "  ✓ Helper: présent"
log ""

# --- X11 ---
GUI_ONLY_BACKEND=true
if [ -n "${DISPLAY:-}" ] && timeout 2 xdpyinfo >/dev/null 2>&1; then
    GUI_ONLY_BACKEND=false
    xhost +local: >/dev/null 2>&1 || log "⚠  xhost indisponible"
    log "  ✓ X11 disponible ($DISPLAY)"
else
    log "⚠  DISPLAY indisponible — test du backend uniquement"
fi

# --- Préparation Docker ---
log "Préparation des fichiers..."
mkdir -p "$DOCKER_DIR"
cp "$MAIN_BIN" "$DOCKER_DIR/modelweaver"
cp "$HELPER" "$DOCKER_DIR/gui_helper.py"
chmod +x "$DOCKER_DIR/modelweaver"

mkdir -p "$DOCKER_DIR/projetclient/sql"
cp "$SCRIPT_DIR/../projetclient/sql/"*.py "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
cp "$SCRIPT_DIR/../projetclient/sql/"*.sql "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
touch "$DOCKER_DIR/projetclient/sql/__init__.py"

cat > "$DOCKER_DIR/Dockerfile" << 'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update && apt-get install -y \
    libgtk-3-0 libgdk-pixbuf-2.0-0 libpango-1.0-0 \
    libcairo2 libatk1.0-0 \
    libwebkit2gtk-4.1-0 libjavascriptcoregtk-4.1-0 \
    libsoup-3.0-0 librsvg2-common \
    libayatana-appindicator3-1 \
    libgl1-mesa-dri \
    dbus-x11 xdg-utils \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /root/.modelweaver
COPY modelweaver /root/.modelweaver/modelweaver
COPY gui_helper.py /root/.modelweaver/gui_helper.py
RUN chmod +x /root/.modelweaver/modelweaver
COPY projetclient /root/.modelweaver/projetclient
ENV HOME=/root
ENV GDK_BACKEND=x11
ENV GTK_MODULES=
ENV LIBGL_ALWAYS_SOFTWARE=1
ENV GALLIUM_DRIVER=llvmpipe
WORKDIR /root/.modelweaver
CMD ["/root/.modelweaver/modelweaver"]
DOCKERFILE

log "Build image Docker..."
docker build -t "$IMAGE" "$DOCKER_DIR" 2>&1

# --- Lancement en mode détaché ---
log "Lancement du conteneur (timeout ${TIMEOUT}s)..."
log ""

set +e
if $GUI_ONLY_BACKEND; then
    docker run --name "$CONTAINER" -d "$IMAGE" \
        /root/.modelweaver/modelweaver --help > /dev/null 2>&1
else
    docker run --name "$CONTAINER" -d \
        -e DISPLAY="$DISPLAY" \
        -v /tmp/.X11-unix:/tmp/.X11-unix \
        "$IMAGE" > /dev/null 2>&1
fi

# Timer de timeout en arrière-plan
(sleep "$TIMEOUT" && echo "[TIMEOUT KILLER]" && docker kill "$CONTAINER" 2>/dev/null) &
KILLER_PID=$!

# Attendre la fin du conteneur
docker wait "$CONTAINER" 2>/dev/null
EXIT_CODE=$?
DURATION=$(( $(date +%s) - START ))

# Arrêter le timer si le conteneur s'est arrêté avant le timeout
kill "$KILLER_PID" 2>/dev/null || true

# Si le conteneur a été tué par le timer, transformer l'exit code en 124
if [ "$DURATION" -ge "$TIMEOUT" ]; then
    EXIT_CODE=124
    TAG="timeout"
    log "⏱  TIMEOUT après ${TIMEOUT}s"
fi

# Logs du conteneur
log "Récupération des logs du conteneur..."
docker logs "$CONTAINER" 2>&1 || true

docker rm -f "$CONTAINER" 2>/dev/null || true

# --- Prompt Y/N/U (seulement si pas de timeout) ---
if [ "$EXIT_CODE" -eq 0 ] && [ -t 0 ] && [ "$TAG" != "timeout" ]; then
    echo ""
    echo -n "Test GUI réussi ? (Y/n/u) ➜ " >&5
    read -r answer < /dev/tty
    echo ""
    case "${answer,,}" in
        n|no|echec) TAG="echec" ;;
        u|unknown)   TAG="unknown" ;;
        *)           TAG="succes"; echo "$DURATION" > "$LAST_FILE" ;;
    esac
elif [ -z "$TAG" ]; then
    [ "$EXIT_CODE" -eq 0 ] && TAG="unknown" || TAG="echec"
fi

[ "$EXIT_CODE" -eq 0 ] && echo "$DURATION" > "$LAST_FILE"

# Finalisation
echo ""
echo "========================================"
echo "  Test GUI terminé"
echo "  Fin: $(date '+%Y-%m-%d %H:%M:%S')"
echo "  Durée: ${DURATION}s"
echo "  Code sortie: $EXIT_CODE"
echo "========================================"

NEW_LOG="$SCRIPT_DIR/log-test-gui-${TAG}-${TIMESTAMP}.log"
mv "$LOG_FILE" "$NEW_LOG" 2>/dev/null || true
echo "  Log: $NEW_LOG"

exit "$EXIT_CODE"
