#!/bin/bash
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
HELPER="$SCRIPT_DIR/gui-main/gui_helper.py"
DOCKER_DIR="$SCRIPT_DIR/docker-cli"
CONTAINER="modelweaver-cli-test"
IMAGE="modelweaver-cli-test"

run_host() {
    echo "============================================"
    echo "  ModelWeaver — Test CLI (hôte)"
    echo "============================================"
    echo ""

    echo "--- 1/6 : Vérification Python ---"
    if command -v python3 &>/dev/null; then
        echo "  ✓ $(python3 --version 2>&1)"
    else
        echo "  ✗ python3 introuvable → installation..."
        sudo apt install -y python3 python3-pip 2>&1 | tail -1
    fi
    echo ""

    echo "--- 2/6 : Vérification SQLite ---"
    if command -v sqlite3 &>/dev/null; then
        echo "  ✓ $(sqlite3 --version 2>&1 | head -1)"
    else
        echo "  ✗ sqlite3 introuvable → installation..."
        sudo apt install -y sqlite3 2>&1 | tail -1
    fi
    echo ""

    echo "--- 3/6 : Initialisation des bases ---"
    python3 "$HELPER" init_databases 2>&1
    echo ""

    echo "--- 4/6 : Dépendances Python ---"
    python3 "$HELPER" check_python_deps 2>&1
    echo ""

    echo "--- 5/6 : Vérification finale ---"
    python3 "$HELPER" check_databases 2>&1
    echo ""

    echo "============================================"
    echo "  Test CLI terminé"
    echo "============================================"
    echo "Logs: ~/.modelweaver/gui.log"
}

run_docker() {
    echo "============================================"
    echo "  ModelWeaver — Test CLI (Docker vierge)"
    echo "============================================"
    echo ""

    mkdir -p "$DOCKER_DIR"

    echo "[INFO] Copie des fichiers..."
    cp "$HELPER" "$DOCKER_DIR/gui_helper.py"

    mkdir -p "$DOCKER_DIR/projetclient/sql"
    cp "$SCRIPT_DIR/../projetclient/sql/"*.py "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
    cp "$SCRIPT_DIR/../projetclient/sql/"*.sql "$DOCKER_DIR/projetclient/sql/" 2>/dev/null || true
    touch "$DOCKER_DIR/projetclient/sql/__init__.py"

    cat > "$DOCKER_DIR/entrypoint.sh" << 'ENTRYPOINT'
#!/bin/bash
set -e

echo "=== ModelWeaver CLI Test (Docker vierge) ==="
echo ""

# Étape 1 : Vérifier/installer Python
echo "--- 1/5 : Python ---"
if command -v python3 &>/dev/null; then
    echo "  ✓ $(python3 --version 2>&1)"
else
    echo "  ✗ introuvable → installation..."
    apt-get update -qq && apt-get install -y -qq python3 python3-pip 2>&1 | tail -1
    echo "  ✓ installé"
fi
echo ""

# Étape 2 : Vérifier/installer SQLite
echo "--- 2/5 : SQLite ---"
if command -v sqlite3 &>/dev/null; then
    echo "  ✓ $(sqlite3 --version 2>&1 | head -1)"
else
    echo "  ✗ introuvable → installation..."
    apt-get install -y -qq sqlite3 2>&1 | tail -1
    echo "  ✓ installé"
fi
echo ""

# Étape 3 : Initialiser les bases
echo "--- 3/5 : Initialisation des bases ---"
if python3 /app/gui_helper.py init_databases; then
    echo "  ✓ modelweaver.db créé"
else
    echo "  ⚠ Échec — on installe python-dotenv et on réessaie"
    pip3 install python-dotenv 2>&1 | tail -1
    python3 /app/gui_helper.py init_databases
    echo "  ✓ modelweaver.db créé"
fi
echo ""

# Étape 4 : Dépendances pip auto-install
echo "--- 4/5 : Dépendances Python ---"
echo "  Détection des déps manquantes..."
DEPS=$(python3 -c "import json; d=json.load(open('/dev/stdin')); [print(r['name']) for r in json.loads(__import__('sys').stdin.read())['deps'] if not r['installed']]" 2>/dev/null <<< "$(python3 /app/gui_helper.py check_python_deps)" || echo "")
if [ -n "$DEPS" ]; then
    echo "  Installation de : $DEPS"
    pip3 install $DEPS 2>&1 | tail -1
    echo "  ✓ déps installées"
    python3 /app/gui_helper.py check_python_deps
else
    echo "  ✓ toutes les déps sont déjà installées"
    python3 /app/gui_helper.py check_python_deps
fi
echo ""

# Étape 5 : Vérification finale
echo "--- 5/5 : Vérification finale ---"
python3 /app/gui_helper.py check_databases
echo ""

echo "=== Test CLI terminé ==="
ENTRYPOINT
    chmod +x "$DOCKER_DIR/entrypoint.sh"

    cat > "$DOCKER_DIR/Dockerfile" << 'DOCKERFILE'
FROM ubuntu:24.04
# Rien d'autre — pas de Python, pas de SQLite, pas de GUI
COPY gui_helper.py /app/gui_helper.py
COPY projetclient /app/projetclient
COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh
CMD ["/entrypoint.sh"]
DOCKERFILE

    echo "[INFO] Build de l'image Docker..."
    docker build -t "$IMAGE" "$DOCKER_DIR" 2>&1 | tail -2

    echo "[INFO] Lancement du conteneur..."
    echo ""
    docker run --rm -i --name "$CONTAINER" "$IMAGE"

    echo ""
    echo "============================================"
    echo "  Test CLI Docker terminé"
    echo "============================================"
}

# --- Option ---
if [ "$1" = "--docker" ]; then
    run_docker
else
    run_host
fi
