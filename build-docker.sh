#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Defaults
FROM_IMAGE="ubuntu-bare"
IMAGE_NAME="model-weaver-v0.1"
SKIP_TINYLLAMA=true
SKIP_WEBUI=true
CONTINUE_CONTAINER=""
SQLITE_MODE=false   # V0.3 : utilise les BDD SQLite au lieu du JSON

usage() {
    cat <<EOF
Usage: $0 [options]

Options:
  --from <image>       Image de base (défaut: ubuntu-bare)
  --name <tag>         Nom de l'image finale (défaut: model-weaver-v0.1)
  --continue <nom>     Reprendre un container existant (saute create/copie)
  --tinyllama          Inclure le téléchargement de tinyllama (~637 Mo)
  --skip-webui         Ignorer l'installation d'Open WebUI
  --sqlite             Utiliser l'architecture SQLite V0.3 (catalogue distant + synchro)
  --help, -h           Affiche cette aide
EOF
    exit 0
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) FROM_IMAGE="$2"; shift 2 ;;
        --name) IMAGE_NAME="$2"; shift 2 ;;
        --continue) CONTINUE_CONTAINER="$2"; shift 2 ;;
        --tinyllama) SKIP_TINYLLAMA=false; shift ;;
        --skip-webui) SKIP_WEBUI=false; shift ;;
        --sqlite) SQLITE_MODE=true; shift ;;
        --help|-h) usage ;;
        *) echo "❌ Argument inconnu: $1"; usage ;;
    esac
done

# ──────────────────────────────────────────────
#  V0.3 : mode SQLite → démarrer le serveur catalogue sur l'hôte
# ──────────────────────────────────────────────
if [ "$SQLITE_MODE" = true ]; then
    REMOTE_DB="$SCRIPT_DIR/.modelweaver/catalogue.remote.db"
    CATALOGUE_PORT=8764
    CATALOGUE_URL="http://host.docker.internal:$CATALOGUE_PORT/api"

    echo "📦 Préparation du catalogue distant..."
    if [ -f "$SCRIPT_DIR/.modelweaver/catalogue.db" ]; then
        cp "$SCRIPT_DIR/.modelweaver/catalogue.db" "$REMOTE_DB"
        echo "   Copié catalogue.db → catalogue.remote.db"
    else
        echo "   ⚠️  catalogue.db introuvable, création d'une base vide"
        python3 -c "
import sqlite3
conn = sqlite3.connect('$REMOTE_DB')
conn.executescript(open('$SCRIPT_DIR/sql/catalogue_schema.sql').read())
conn.close()
"
    fi

    echo "🖥️  Démarrage du serveur catalogue sur le port $CATALOGUE_PORT..."
    python3 "$SCRIPT_DIR/sql/catalogue_server.py" --port "$CATALOGUE_PORT" --db "$REMOTE_DB" &
    CATALOGUE_PID=$!
    sleep 1

    _cleanup_catalogue() {
        kill "$CATALOGUE_PID" 2>/dev/null || true
        rm -f "$REMOTE_DB" 2>/dev/null || true
    }

    if ! curl -sf "http://localhost:$CATALOGUE_PORT/health" >/dev/null 2>&1; then
        echo "❌ Le serveur catalogue ne répond pas sur le port $CATALOGUE_PORT"
        exit 1
    fi
    echo "   ✅ Serveur catalogue OK"
fi

if [ -z "$CONTINUE_CONTAINER" ]; then
    CONTAINER_NAME="mw-build-$(date +%s)"

    if ! docker image inspect "$FROM_IMAGE" &>/dev/null; then
        echo "❌ Image de base introuvable : $FROM_IMAGE"
        echo "   Construisez-la d'abord avec : docker build -t $FROM_IMAGE -f- . <<<'FROM ubuntu:24.04'"
        exit 1
    fi

    # Caches persistants
    MW_CACHE_DIR="$SCRIPT_DIR/.modelweaver/cache"
    APT_CACHE_DIR="$SCRIPT_DIR/.modelweaver/apt-cache"
    APT_LISTS_DIR="$SCRIPT_DIR/.modelweaver/apt-lists"
    mkdir -p "$MW_CACHE_DIR" "$APT_CACHE_DIR" "$APT_LISTS_DIR"

    MW_ARGS="--mode YES --skip-audit"
    if [ "$SKIP_TINYLLAMA" = true ]; then
        MW_ARGS="$MW_ARGS --skip-tinyllama"
    fi
    if [ "$SKIP_WEBUI" = true ]; then
        MW_ARGS="$MW_ARGS --skip-webui"
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Build ModelWeaver Docker Image"
    echo "  Base  : $FROM_IMAGE"
    echo "  Cible : $IMAGE_NAME"
    echo "  Mode  : $([ "$SQLITE_MODE" = true ] && echo 'SQLite V0.3' || echo 'classique')"
    echo "  Tiny  : $([ "$SKIP_TINYLLAMA" = true ] && echo 'non' || echo 'oui')"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$SQLITE_MODE" = true ]; then
        trap '_cleanup_catalogue; docker rm -f "$CONTAINER_NAME" 2>/dev/null || true' EXIT
    else
        cleanup() {
            docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
        }
        trap cleanup EXIT
    fi

    # Création du container (avec CATALOGUE_URL si mode SQLite)
    echo "🚀 Création du container..."
    DOCKER_ENVS=(
        --env PYTHONUNBUFFERED=1
        --env DEBIAN_FRONTEND=noninteractive
    )
    if [ "$SQLITE_MODE" = true ]; then
        DOCKER_ENVS+=(--env "CATALOGUE_URL=$CATALOGUE_URL")
    fi
    docker create \
        --workdir /app \
        --name "$CONTAINER_NAME" \
        "${DOCKER_ENVS[@]}" \
        --add-host host.docker.internal:host-gateway \
        --volume "$APT_CACHE_DIR:/var/cache/apt/archives" \
        --volume "$APT_LISTS_DIR:/var/lib/apt/lists" \
        --volume "$MW_CACHE_DIR:/app/.modelweaver/cache" \
        "$FROM_IMAGE" \
        sleep 9999 >/dev/null

    # Copie des fichiers du projet
    echo "📂 Copie des fichiers du projet..."
    tar cf - \
        --exclude='.opencode' \
        --exclude='.modelweaver' \
        --exclude='docker-backup' \
        --exclude='__pycache__' \
        --exclude='node_modules' \
        -C "$SCRIPT_DIR" . | docker cp - "$CONTAINER_NAME:/app"

    # Copie du .env si présent
    if [ -f "$SCRIPT_DIR/.env" ]; then
        echo "🔑 Copie de .env..."
        docker cp "$SCRIPT_DIR/.env" "$CONTAINER_NAME:/app/.env"
    fi

    # Mode YES
    echo "YES" > /tmp/.mw_config_build
    docker cp /tmp/.mw_config_build "$CONTAINER_NAME:/app/.modelweaver_config"
    rm -f /tmp/.mw_config_build
else
    CONTAINER_NAME="$CONTINUE_CONTAINER"

    if ! docker inspect "$CONTAINER_NAME" &>/dev/null; then
        echo "❌ Container introuvable : $CONTAINER_NAME"
        exit 1
    fi

    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  Reprise du container $CONTAINER_NAME"
    echo "  Cible : $IMAGE_NAME"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

    if [ "$SQLITE_MODE" = true ]; then
        trap '_cleanup_catalogue; true' EXIT
    else
        cleanup() {
            true  # ne pas supprimer le container en mode --continue
        }
        trap cleanup EXIT
    fi
fi

# ──────────────────────────────────────────────
#  Exécution
# ──────────────────────────────────────────────
docker start "$CONTAINER_NAME" >/dev/null

if [ "$SQLITE_MODE" = true ]; then
    echo "▶️  Installation SQLite (install_in_docker.py)..."
    EXIT_CODE=0
    docker exec "$CONTAINER_NAME" python3 /app/install_in_docker.py || EXIT_CODE=$?

    if [ $EXIT_CODE -ne 0 ]; then
        echo "❌ Installation SQLite échouée (code $EXIT_CODE)."
        echo "   Pour déboguer : docker start -ai $CONTAINER_NAME"
        exit $EXIT_CODE
    fi
else
    echo "▶️  Installation classique (cette étape peut prendre 5-15 minutes)..."
    EXIT_CODE=0
    docker exec "$CONTAINER_NAME" bash /app/modelweaver.sh $MW_ARGS || EXIT_CODE=$?

    if [ $EXIT_CODE -ne 0 ]; then
        echo "❌ Installation échouée (code $EXIT_CODE)."
        echo "   Pour déboguer : docker start -ai $CONTAINER_NAME"
        echo "   Pour reprendre : $0 --continue $CONTAINER_NAME --name $IMAGE_NAME"
        exit $EXIT_CODE
    fi

    # Pipeline de configuration API et routage
    if [ -f "$SCRIPT_DIR/.env" ]; then
        echo "🔧  Configuration du routage et du fallback..."
        MW_DIR="$SCRIPT_DIR/.modelweaver"
        for f in fallback_preferences.yaml model_scores.json litellm_router_proxy.py cache/models_api.json; do
            [ -f "$MW_DIR/$f" ] && docker cp "$MW_DIR/$f" "$CONTAINER_NAME:/app/.modelweaver/$f"
        done
        echo "   🌐  Synchronisation des modèles et configuration du routage..."
        PYCMD="python3"
        docker exec "$CONTAINER_NAME" bash -c '[ -x /opt/python3.10-static/python/bin/python3 ] && echo "static" || echo "system"' > /tmp/.mw_pycheck 2>/dev/null || true
        if grep -q "static" /tmp/.mw_pycheck 2>/dev/null; then
            PYCMD="/opt/python3.10-static/python/bin/python3"
        fi
        rm -f /tmp/.mw_pycheck
        docker exec "$CONTAINER_NAME" bash -c "
            cd /app
            $PYCMD maj-liste-litellm.py --skip-fetch 2>&1 | tail -5
            $PYCMD -c \"
import yaml
with open('/app/.modelweaver/litellm_config.yaml') as f:
    cfg = yaml.safe_load(f)
if 'context_settings' in cfg:
    cfg['context_settings']['project_root'] = '/app'
with open('/app/.modelweaver/litellm_config.yaml', 'w') as f:
    yaml.dump(cfg, f, default_flow_style=False)
print('   ✅  Configuration du routage terminée')
\"
            echo \"   📊  \$(wc -l < /app/.modelweaver/litellm_config.yaml) lignes dans litellm_config.yaml\"
        " 2>&1
    fi
fi

# Commit de l'image
echo "📸 Commit de l'image $IMAGE_NAME..."
docker commit "$CONTAINER_NAME" "$IMAGE_NAME" >/dev/null

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "✅ Image $IMAGE_NAME créée avec succès"
SIZE=$(docker images --format "{{.Size}}" "$IMAGE_NAME" 2>/dev/null || echo "?")
echo "   Taille : $SIZE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
