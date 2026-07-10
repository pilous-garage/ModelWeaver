#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$SCRIPT_DIR/docker"
MAIN_BIN="$SCRIPT_DIR/gui-main/src-tauri/target/release/modelweaver"
HELPER="$SCRIPT_DIR/gui-main/gui_helper.py"
CONTAINER="modelweaver-test"
IMAGE="modelweaver-test"

# Options
CLEAN=false
for arg in "$@"; do
    [ "$arg" = "--clean" ] && CLEAN=true
done

container_exists() { docker ps -a --format "{{.Names}}" | grep -qx "$CONTAINER"; }
container_running() { docker ps --format "{{.Names}}" | grep -qx "$CONTAINER"; }

# --clean : supprimer et recréer
if $CLEAN; then
    echo "[INFO] Option --clean : suppression de l'ancien conteneur..."
    docker rm -f "$CONTAINER" 2>/dev/null || true
fi

# Vérifier l'état du conteneur
if container_running; then
    echo "[INFO] Le conteneur '$CONTAINER' est déjà en cours d'exécution."
    echo "       Pour l'arrêter : docker stop $CONTAINER"
    echo "       Pour recréer : $0 --clean"
    exit 0
fi

if container_exists; then
    echo "[INFO] Conteneur '$CONTAINER' existe mais est arrêté. Redémarrage..."
    echo "[INFO] Pour récupérer les logs : ./projetadmin/get-log-docker-test.sh"
    xhost +local: >/dev/null 2>&1 || true
    docker start -ai "$CONTAINER"
    exit $?
fi

# --- Créer un nouveau conteneur ---

mkdir -p "$DOCKER_DIR"

echo "[INFO] Vérification des fichiers requis..."
[ -f "$MAIN_BIN" ] || { echo "[ERROR] Main introuvable : $MAIN_BIN"; echo "       Lance d'abord : cd gui-main && npm run tauri build"; exit 1; }
[ -f "$HELPER" ] || { echo "[ERROR] Helper introuvable : $HELPER"; exit 1; }

echo "[INFO] Préparation des fichiers dans $DOCKER_DIR..."
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

echo "[INFO] Build de l'image Docker..."
docker build -t "$IMAGE" "$DOCKER_DIR" 2>&1 | tail -3

echo "[INFO] Configuration X11..."
xhost +local: >/dev/null 2>&1 || true

echo "[INFO] Lancement du nouveau conteneur '$CONTAINER'..."
echo "[INFO] Pour arrêter : docker stop $CONTAINER"
echo "[INFO] Pour relancer : $0"
echo "[INFO] Pour recréer : $0 --clean"
echo "[INFO] Pour récupérer les logs : ./projetadmin/get-log-docker-test.sh"

docker run -it \
    --name "$CONTAINER" \
    -e DISPLAY=$DISPLAY \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    "$IMAGE"
