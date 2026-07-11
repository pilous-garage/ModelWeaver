#!/bin/bash

# Script to test dependency installation in Docker
TIMEOUT=${1:-60}  # Default: 60s, max: 900s
LOG_FILE="/tmp/modelweaver-install-docker-test-$(date +%s).log"
CONTAINER_NAME="mw-gui-test"

# Clean up previous logs
rm -f "$LOG_FILE"

echo "Starting ModelWeaver installation test in Docker (timeout: ${TIMEOUT}s)"
echo "Logs will be saved to: $LOG_FILE"

# Function to log with timestamp
log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

# Check if container exists
if ! docker inspect "$CONTAINER_NAME" >/dev/null 2>&1; then
    log "ERROR: Container $CONTAINER_NAME does not exist"
    exit 1
fi

# Remove nonexistentdep from dependencies (for Docker test)
log "Removing nonexistentdep from dependencies for Docker test"
docker exec "$CONTAINER_NAME" bash -c "\
    cd /app/projetadmin/gui-main/src/dependencies && \
    jq 'del(.required[] | select(.name == "nonexistentdep"))' dependencies_linux.json > tmp.json && \
    mv tmp.json dependencies_linux.json
"

# Add keyring to pip packages
log "Adding keyring to pip packages"
docker exec "$CONTAINER_NAME" bash -c "\
    cd /app/projetadmin/gui-main/src/dependencies && \
    jq '.required[0].pip_packages += ["keyring"]' dependencies_linux.json > tmp.json && \
    mv tmp.json dependencies_linux.json
"

# Install missing dependencies in container
log "Installing missing dependencies in container"
docker exec "$CONTAINER_NAME" bash -c "\
    apt-get update && \
    apt-get install -y curl git python3 python3-pip && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y && \
    export PATH=\"/root/.cargo/bin:\".$PATH && \
    cd /app/projetadmin/gui-main && \
    npm install
" 2>&1 | while read -r line; do log "DEPENDENCY INSTALL: $line"; done

# Start the GUI test with timeout
log "Starting GUI test with timeout: ${TIMEOUT}s"
timeout $TIMEOUT bash -c "\
    docker exec -e DISPLAY -e QT_DEBUG_PLUGINS=1 "$CONTAINER_NAME" bash -c \
        'cd /app/projetadmin/gui-main && \
         xvfb-run -a npm run tauri dev' \
    2>&1 | while read -r line; do log "GUI: $line"; done
" || {
    EXIT_CODE=$?
    log "Test completed with exit code: $EXIT_CODE"
    
    # Check if timeout was reached
    if [ $EXIT_CODE -eq 124 ]; then
        log "ERROR: Test timed out after ${TIMEOUT} seconds"
    else
        log "Test completed (exit code: $EXIT_CODE)"
    fi
}

# Check if installation was triggered (look for "Install Selected" click)
if grep -q "Install Selected" "$LOG_FILE"; then
    log "SUCCESS: Installation was triggered"
else
    log "ERROR: Installation was not triggered"
    exit 1
fi

log "Test completed. Logs: $LOG_FILE"