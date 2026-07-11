#!/bin/bash

# Script to test ModelWeaver GUI in Docker
TIMEOUT=120
LOG_FILE="/tmp/modelweaver-gui-docker-test-$(date +%s).log"
IMAGE_NAME="modelweaver-gui-test"
CONTAINER_NAME="modelweaver-gui-test-container"

# Clean up previous runs
docker stop $CONTAINER_NAME 2>/dev/null || true
docker rm $CONTAINER_NAME 2>/dev/null || true
docker rmi $IMAGE_NAME 2>/dev/null || true

echo "Building Docker image..."
echo "Logs will be saved to: $LOG_FILE"

# Build Docker image
docker build -t $IMAGE_NAME -f - . <<EOF
FROM ubuntu:22.04

# Install dependencies
RUN apt-get update && apt-get install -y \
    curl \
    git \
    python3 \
    python3-pip \
    sqlite3 \
    xvfb \
    libgtk-3-dev \
    libwebkit2gtk-4.0-dev \
    libayatana-appindicator3-dev \
    librsvg2-dev \
    wget

# Install Node.js and npm
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
RUN apt-get install -y nodejs

# Install Rust
RUN curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y
ENV PATH="/root/.cargo/bin:\$PATH"

# Copy the project
WORKDIR /app
COPY . /app

# Install frontend dependencies
RUN cd projetadmin/gui-main && npm install

# Build the app
RUN cd projetadmin/gui-main && npm run tauri build

# Command to run the test
CMD ["bash", "-c", "cd /app/projetadmin/gui-main && xvfb-run -a npm run tauri dev"]
EOF

if [ $? -ne 0 ]; then
    echo "ERROR: Failed to build Docker image"
    exit 1
fi

echo "Starting Docker container with timeout: ${TIMEOUT}s..."

# Run the container with timeout
timeout $TIMEOUT docker run --name $CONTAINER_NAME \
    -e DISPLAY \
    -v /tmp/.X11-unix:/tmp/.X11-unix \
    --security-opt seccomp=unconfined \
    $IMAGE_NAME 2>&1 | tee "$LOG_FILE"

EXIT_CODE=$?

echo "Test completed with exit code: $EXIT_CODE"

# Check if timeout was reached
if [ $EXIT_CODE -eq 124 ]; then
    echo "ERROR: Test timed out after ${TIMEOUT} seconds"
    echo "Check logs: $LOG_FILE"
    exit 1
else
    echo "Test completed (exit code: $EXIT_CODE)"
    echo "Logs: $LOG_FILE"
fi

# Check if the GUI started correctly (look for "ModelWeaver" in logs)
if grep -q "ModelWeaver" "$LOG_FILE"; then
    echo "SUCCESS: GUI started correctly"
else
    echo "ERROR: GUI did not start correctly"
    exit 1
fi