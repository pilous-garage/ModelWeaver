#!/bin/bash

# Script to test the main GUI with timeout
TIMEOUT=60
LOG_FILE="/tmp/modelweaver-gui-test-$(date +%s).log"

echo "Starting ModelWeaver GUI test (timeout: ${TIMEOUT}s)"
echo "Logs will be saved to: ${LOG_FILE}"

# Change to the main app directory
cd "$(dirname "$0")/projetadmin/gui-main" || exit 1

# Run with timeout and capture output
timeout ${TIMEOUT} bash -c "\
  export PATH=\"$HOME/.cargo/bin:\$PATH\" && \
  npm run tauri dev 2>&1 | tee \"${LOG_FILE}\" \
" || {
  EXIT_CODE=$?
  echo "Test completed with exit code: ${EXIT_CODE}"
  
  # Check if timeout was reached
  if [ $EXIT_CODE -eq 124 ]; then
    echo "ERROR: Test timed out after ${TIMEOUT} seconds"
    echo "Check logs: ${LOG_FILE}"
  else
    echo "Test completed (exit code: ${EXIT_CODE})"
    echo "Logs: ${LOG_FILE}"
  fi
  
  exit $EXIT_CODE
}