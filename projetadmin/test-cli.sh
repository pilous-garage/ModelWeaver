#!/bin/bash
set -euo pipefail

SHELL_SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
DOCKER_DIR="$SHELL_SCRIPT_DIR/docker-cli"
IMAGE="modelweaver-cli-test"
CONTAINER="modelweaver-cli-test"
LAST_FILE="$SHELL_SCRIPT_DIR/.last-test-cli-time"

TIMEOUT_DEFAULT=60
TIMESTAMP=$(date +%Y%m%d%H%M%S)
TAG=""

ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

MODE="docker"
for arg in "$@"; do
    case "$arg" in
        --timeout=*) TIMEOUT="${arg#*=}" ;;
        --docker) MODE="docker" ;;
        --host) MODE="host" ;;
    esac
done

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
LOG_FILE="$SHELL_SCRIPT_DIR/log-test-cli-${TIMESTAMP}.log"

{
    echo "ModelWeaver — test-cli.sh (API daemon)"
    echo "Timestamp: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "Timeout: ${TIMEOUT}s"
    echo ""
} > "$LOG_FILE"

cleanup() {
    local ec=$?
    docker rm -f "$CONTAINER" 2>/dev/null || true
    [ -d "$DOCKER_DIR" ] && rm -rf "$DOCKER_DIR"
    return "$ec"
}

DAEMON_URL="http://127.0.0.1:8770/v1"

wait_for_daemon() {
    local max_wait=30
    local waited=0
    log "Attente du daemon..."
    while [ $waited -lt $max_wait ]; do
        if curl -s -o /dev/null -w "" "$DAEMON_URL/version" 2>/dev/null; then
            log "  ✓ daemon prêt"
            return 0
        fi
        sleep 1
        waited=$((waited + 1))
    done
    log "  ✗ daemon non prêt après ${max_wait}s"
    return 1
}

api_call() {
    local method="$1"
    local route="$2"
    local body="$3"
    curl -s -X POST "${DAEMON_URL}/${route}" \
        -H "Content-Type: application/json" \
        -d "${body}" 2>&1
}

run_host() {
    log "Mode hôte — API daemon directe"
    log "Démarrage de modelweaver en arrière-plan..."
    "$SHELL_SCRIPT_DIR/../gui-main/src-tauri/target/release/modelweaver" &
    local mw_pid=$!
    trap "kill $mw_pid 2>/dev/null || true" EXIT
    sleep 3

    if ! wait_for_daemon; then
        kill "$mw_pid" 2>/dev/null || true
        return 1
    fi

    log "1/3 : Init DB (db/init)"
    local r1
    r1=$(api_call POST "db/init" '{}')
    log "  $r1"

    log "2/3 : Dépendances (system/deps/check)"
    local r2
    r2=$(api_call POST "system/deps/check" '{}')
    log "  $r2"

    log "3/3 : Vérification finale (db/check)"
    local r3
    r3=$(api_call POST "db/check" '{}')
    log "  $r3"

    kill "$mw_pid" 2>/dev/null || true
    wait "$mw_pid" 2>/dev/null || true
}

run_docker() {
    log "Mode Docker — API daemon directe"

    cat > "$DOCKER_DIR/Dockerfile" << 'DOCKERFILE'
FROM ubuntu:24.04
RUN apt-get update -qq && apt-get install -y -qq \
    libgtk-3-0 libgdk-pixbuf-2.0-0 libpango-1.0-0 \
    libcairo2 libatk1.0-0 \
    libwebkit2gtk-4.1-0 libjavascriptcoregtk-4.1-0 \
    libsoup-3.0-0 librsvg2-common \
    libayatana-appindicator3-1 \
    libgl1-mesa-dri dbus-x11 xdg-utils curl \
    && rm -rf /var/lib/apt/lists/*
RUN mkdir -p /root/.modelweaver
COPY modelweaver /root/.modelweaver/modelweaver
RUN chmod +x /root/.modelweaver/modelweaver
ENV HOME=/root
WORKDIR /root/.modelweaver
CMD ["./modelweaver"]
DOCKERFILE

    cat > "$DOCKER_DIR/entrypoint.sh" << 'ENTRYPOINT'
#!/bin/bash
set -e
ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
log "=== ModelWeaver CLI Test (Docker, API daemon) ==="
log ""
log "Démarrage modelweaver..."
/root/.modelweaver/modelweaver &
MW_PID=$!
sleep 4

DAEMON_URL="http://127.0.0.1:8770/v1"
wait_for() {
    for i in $(seq 1 30); do
        if curl -s -o /dev/null "$DAEMON_URL/version" 2>/dev/null; then
            log "  ✓ daemon prêt"; return 0
        fi
        sleep 1
    done
    log "  ✗ daemon injoignable"; return 1
}

if ! wait_for; then
    log "ERREUR: daemon non prêt"
    kill $MW_PID 2>/dev/null || true
    exit 1
fi

log "1/3 : Init DB (db/init)"
curl -s -X POST "$DAEMON_URL/db/init" -H "Content-Type: application/json" -d '{}' | log "  réponse:"

log "2/3 : Dépendances (system/deps/check)"
curl -s -X POST "$DAEMON_URL/system/deps/check" -H "Content-Type: application/json" -d '{}' | log "  réponse:"

log "3/3 : Vérification finale (db/check)"
curl -s -X POST "$DAEMON_URL/db/check" -H "Content-Type: application/json" -d '{}' | log "  réponse:"

log "=== Test CLI terminé ==="
kill $MW_PID 2>/dev/null || true
ENTRYPOINT
    chmod +x "$DOCKER_DIR/entrypoint.sh"

    cp "$SHELL_SCRIPT_DIR/../gui-main/src-tauri/target/release/modelweaver" "$DOCKER_DIR/modelweaver" 2>/dev/null || {
        log "✗ Binaire modelweaver introuvable, build requis"
        log "  → Build : cd gui-main && npm run tauri build"
        return 1
    }
    chmod +x "$DOCKER_DIR/modelweaver"

    log "Build image Docker..."
    docker build -t "$IMAGE" "$DOCKER_DIR" 2>&1

    log "Lancement du conteneur (timeout ${TIMEOUT}s)..."
    timeout "$TIMEOUT" docker run --name "$CONTAINER" -i "$IMAGE" 2>&1 && return 0
    local ec=$?
    if [ "$ec" -eq 124 ]; then
        log "⏱  TIMEOUT après ${TIMEOUT}s"
        docker kill "$CONTAINER" 2>/dev/null || true
        docker wait "$CONTAINER" 2>/dev/null || true
        timeout 5 docker logs "$CONTAINER" 2>/dev/null || true
    fi
    docker rm -f "$CONTAINER" 2>/dev/null || true
    return "$ec"
}

trap cleanup EXIT
docker rm -f "$CONTAINER" 2>/dev/null || true

set +e
if [ "$MODE" = "docker" ]; then
    run_docker 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=${PIPESTATUS[0]}
else
    run_host 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=${PIPESTATUS[0]}
fi
DURATION=$(( $(date +%s) - START ))
set -e

if [ "$EXIT_CODE" -eq 124 ]; then
    TAG="timeout"
elif [ "$EXIT_CODE" -eq 0 ]; then
    if [ -t 0 ]; then
        echo "" | tee -a "$LOG_FILE"
        echo -n "Test réussi ? (Y/n/u) › " | tee /dev/stderr
        read -r answer < /dev/tty
        echo ""
        case "${answer,,}" in
            n|no|echec) TAG="echec" ;;
            u|unknown)   TAG="unknown" ;;
            *)           TAG="succes"; echo "$DURATION" > "$LAST_FILE" ;;
        esac
    else
        TAG="unknown"
    fi
else
    TAG="echec"
fi

[ "$EXIT_CODE" -eq 0 ] && echo "$DURATION" > "$LAST_FILE"

{
    echo ""
    echo "========================================"
    echo "  Test CLI terminé"
    echo "  Fin: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Durée: ${DURATION}s"
    echo "  Code sortie: $EXIT_CODE"
    echo "========================================"
} | tee -a "$LOG_FILE"

NEW_LOG="$SHELL_SCRIPT_DIR/log-test-cli-${TAG}-${TIMESTAMP}.log"
mv "$LOG_FILE" "$NEW_LOG" 2>/dev/null || true
echo "  Log: $NEW_LOG"

exit "$EXIT_CODE"