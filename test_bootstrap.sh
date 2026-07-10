#!/bin/bash
set -e

echo "--- [SIMULATION BOOTSTRAP GUI] ---"

# 1. Simulation du téléchargement et extraction (ce que fait Rust)
echo "Step 1: Downloading and unpacking project..."
mkdir -p ~/.modelweaver
# Note: On utilise l'archive locale pour le test si on est déjà dans le repo, 
# sinon on utiliserait curl.
if [ -f "modelweaver_client.tar.gz" ]; then
    tar -xzf modelweaver_client.tar.gz -C ~/.modelweaver
else
    echo "Downloading from GitHub..."
    curl -L https://github.com/pilous-garage/ModelWeaver/releases/latest/download/modelweaver-main-linux-x86_64.tar.gz -o /tmp/mw.tar.gz
    tar -xzf /tmp/mw.tar.gz -C ~/.modelweaver
fi

# 2. Exécution du script de bootstrap (ce que fait Rust via run_bootstrap_script)
echo "Step 2: Running system bootstrap..."
bash ~/.modelweaver/modelweaver.sh --autoinstall

echo "--- [BOOTSTRAP COMPLETED] ---"
ls -la ~/.modelweaver
