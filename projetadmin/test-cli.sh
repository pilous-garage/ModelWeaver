#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/gui-main/gui_helper.py"
DOCKER_DIR="$SCRIPT_DIR/docker-cli"
IMAGE="modelweaver-cli-test"
CONTAINER="modelweaver-cli-test"
LAST_FILE="$SCRIPT_DIR/.last-test-cli-time"

TIMEOUT_DEFAULT=180
TIMESTAMP=$(date +%Y%m%d%H%M%S)
TAG=""

ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }

# --- Parse arguments ---
MODE="docker"
for arg in "$@"; do
    case "$arg" in
        --timeout=*) TIMEOUT="${arg#*=}" ;;
        --docker) MODE="docker" ;;
        --host) MODE="host" ;;
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
LOG_FILE="$SCRIPT_DIR/log-test-cli-${TIMESTAMP}.log"

# Write header (duration will be inserted at the end)
{
    echo "ModelWeaver — test-cli.sh"
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


run_host() {
    log "Mode hôte"
    log "Python..."
    if command -v python3 &>/dev/null; then
        log "  ✓ $(python3 --version 2>&1)"
    else
        log "  ✗ python3 → installation..."
        sudo apt install -y python3 python3-pip 2>&1 | tail -1
    fi
    log "SQLite..."
    if command -v sqlite3 &>/dev/null; then
        log "  ✓ $(sqlite3 --version 2>&1 | head -1)"
    else
        log "  ✗ sqlite3 → installation..."
        sudo apt install -y sqlite3 2>&1 | tail -1
    fi
    log "Init DB..."
    python3 "$HELPER" init_databases 2>&1
    log "Dépendances Python..."
    python3 "$HELPER" check_python_deps 2>&1
    log "Vérification finale..."
    python3 "$HELPER" check_databases 2>&1
}

run_docker() {
    log "Mode Docker vierge"

    mkdir -p "$DOCKER_DIR"
    cp "$HELPER" "$DOCKER_DIR/gui_helper.py"
    mkdir -p "$DOCKER_DIR/projetclient/sql"
    cp "$SCRIPT_DIR/../projetclient/sql/"*.py "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
    cp "$SCRIPT_DIR/../projetclient/sql/"*.sql "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
    touch "$DOCKER_DIR/projetclient/sql/__init__.py"

    cat > "$DOCKER_DIR/entrypoint.sh" << 'ENTRYPOINT'
#!/bin/bash
set -e
ts() { date '+%H:%M:%S'; }
log() { echo "[$(ts)] $*"; }
log "=== ModelWeaver CLI Test (Docker vierge) ==="
log ""
log "1/5 : Python"
if command -v python3 &>/dev/null; then log "  ✓ $(python3 --version 2>&1)"; else log "  ✗ → installation..."; apt-get update -qq && apt-get install -y -qq python3 python3-pip 2>&1 | tail -1; log "  ✓ installé"; fi
log ""
log "2/5 : SQLite"
if command -v sqlite3 &>/dev/null; then log "  ✓ $(sqlite3 --version 2>&1 | head -1)"; else log "  ✗ → installation..."; apt-get install -y -qq sqlite3 2>&1 | tail -1; log "  ✓ installé"; fi
log ""
log "3/5 : Initialisation des bases"; python3 /app/gui_helper.py init_databases; log ""
log "4/5 : Dépendances Python"; python3 /app/gui_helper.py check_python_deps; log ""
log "5/5 : Vérification finale"; python3 /app/gui_helper.py check_databases; log ""
log "=== Test CLI terminé ==="
ENTRYPOINT
    chmod +x "$DOCKER_DIR/entrypoint.sh"

    cat > "$DOCKER_DIR/Dockerfile" << 'DOCKERFILE'
FROM ubuntu:24.04
COPY gui_helper.py /app/gui_helper.py
COPY projetclient /app/projetclient
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
DOCKERFILE

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

# --- MAIN ---
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

# Determine tag from exit code
if [ "$EXIT_CODE" -eq 124 ]; then
    TAG="timeout"
elif [ "$EXIT_CODE" -eq 0 ]; then
    if [ -t 0 ]; then
        echo "" | tee -a "$LOG_FILE"
        echo -n "Test réussi ? (Y/n/u) ➜ " | tee /dev/stderr
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

# Finalisation
{
    echo ""
    echo "========================================"
    echo "  Test CLI terminé"
    echo "  Fin: $(date '+%Y-%m-%d %H:%M:%S')"
    echo "  Durée: ${DURATION}s"
    echo "  Code sortie: $EXIT_CODE"
    echo "========================================"
} | tee -a "$LOG_FILE"

NEW_LOG="$SCRIPT_DIR/log-test-cli-${TAG}-${TIMESTAMP}.log"
mv "$LOG_FILE" "$NEW_LOG" 2>/dev/null || true
echo "  Log: $NEW_LOG"

exit "$EXIT_CODE"
