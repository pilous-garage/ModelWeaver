#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
LEVELS_FILE="$SCRIPT_DIR/docker-backup/levels.json"
BACKUP_DIR="$SCRIPT_DIR/docker-backup"

FROM_LEVEL="bare"
MODE="check"
SAVE_ON_SUCCESS=false
EXTRA_ARGS=""

usage() {
    echo "Usage: $0 [--from <level>] [--mode <mode>] [--save] [--args <args>]"
    echo "  --from <level>   Niveau de base (bare, python, ollama, openwebui)  [default: bare]"
    echo "  --mode <mode>    Mode de test (check, auto, ask)                   [default: check]"
    echo "  --save           Sauvegarder le snapshot si le test réussit"
    echo "  --args <args>    Arguments supplémentaires pour modelweaver.py     [default: '']"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --from) FROM_LEVEL="$2"; shift 2 ;;
        --mode) MODE="$2"; shift 2 ;;
        --save) SAVE_ON_SUCCESS=true; shift ;;
        --args) EXTRA_ARGS="$2"; shift 2 ;;
        --help|-h) usage ;;
        *) echo "❌ Argument inconnu: $1"; usage ;;
    esac
done

# Traduit le mode en valeur pour .modelweaver_config
mode_value() {
    case "$1" in
        check) echo "NO" ;;
        auto)  echo "YES" ;;
        ask)   echo "ASK" ;;
        *)     echo "YES" ;;
    esac
}

# Récupère la config d'un niveau depuis levels.json
level_info() {
    python3 -c "
import json, sys
with open('$LEVELS_FILE') as f:
    data = json.load(f)
for l in data['levels']:
    if l['id'] == '$FROM_LEVEL':
        print(json.dumps({'index': l.get('index', -1), 'tar': l.get('tar', '')}))
        sys.exit(0)
print(json.dumps({'index': -1, 'tar': ''}))
"
}

INFO=$(level_info)
INDEX=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['index'])")
TAR_REL=$(echo "$INFO" | python3 -c "import json,sys; print(json.load(sys.stdin)['tar'])")

if [ -z "$TAR_REL" ]; then
    echo "❌ Niveau '$FROM_LEVEL' inconnu."
    python3 -c "
import json
with open('$LEVELS_FILE') as f:
    for l in json.load(f)['levels']:
        print(f'  - {l[\"id\"]}: {l[\"description\"]}')
"
    exit 1
fi

IMAGE_TAG="modelweaver-bak:${FROM_LEVEL}"
TAR_PATH="$BACKUP_DIR/$(basename "$TAR_REL")"
CONTAINER_NAME="modelweaver-test-${FROM_LEVEL}-$(date +%s)"

cleanup() {
    docker rm -f "$CONTAINER_NAME" 2>/dev/null || true
}
trap cleanup EXIT

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  Niveau : $FROM_LEVEL  |  Mode : $MODE"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- Charger ou construire l'image de base ---
if docker image inspect "$IMAGE_TAG" &>/dev/null; then
    echo "♻️  Image locale $IMAGE_TAG réutilisée."
elif [ -f "$TAR_PATH" ]; then
    echo "📦 Chargement depuis $TAR_PATH..."
    docker load -i "$TAR_PATH" >/dev/null
    docker tag "$(docker images --filter "reference=*${FROM_LEVEL}*" -q | head -1)" "$IMAGE_TAG" 2>/dev/null || true
elif [ "$FROM_LEVEL" = "bare-old" ]; then
    echo "🔨 Build de l'image vieille (20.04)..."
    docker build --pull -t "$IMAGE_TAG" -f- "$SCRIPT_DIR" <<'DOCKERFILE' 2>&1 | grep -vE 'DEPRECATED|Install the buildx' || true
FROM ubuntu:20.04
DOCKERFILE
    if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
        echo "❌ Échec du build."
        exit 1
    fi
elif [ "$FROM_LEVEL" = "bare" ]; then
    echo "🔨 Build de l'image vierge (24.04)..."
    docker build --pull -t "$IMAGE_TAG" -f- "$SCRIPT_DIR" <<'DOCKERFILE' 2>&1 | grep -vE 'DEPRECATED|Install the buildx' || true
FROM ubuntu:24.04
DOCKERFILE
    if ! docker image inspect "$IMAGE_TAG" &>/dev/null; then
        echo "❌ Échec du build."
        exit 1
    fi
else
    echo "❌ Aucune source pour $FROM_LEVEL."
    echo "   Exécute d'abord: $0 --from bare --mode auto --save"
    exit 1
fi

# --- Caches persistant entre les tests ---
APT_CACHE_DIR="$SCRIPT_DIR/.modelweaver/apt-cache"
APT_LISTS_DIR="$SCRIPT_DIR/.modelweaver/apt-lists"
MW_CACHE_DIR="$SCRIPT_DIR/.modelweaver/cache"
mkdir -p "$APT_CACHE_DIR" "$APT_LISTS_DIR" "$MW_CACHE_DIR"

# --- Création du container ---
echo "🚀 Création du container $CONTAINER_NAME..."
docker create \
    --workdir /app \
    --label modelweaver \
    --name "$CONTAINER_NAME" \
    --env PYTHONUNBUFFERED=1 \
    --env DEBIAN_FRONTEND=noninteractive \
    --volume "$APT_CACHE_DIR:/var/cache/apt/archives" \
    --volume "$APT_LISTS_DIR:/var/lib/apt/lists" \
    --volume "$MW_CACHE_DIR:/app/.modelweaver/cache" \
    "$IMAGE_TAG" bash /app/modelweaver.sh $EXTRA_ARGS

echo "📂 Copie des fichiers du projet..."
tar cf - \
  --exclude='.opencode' \
  --exclude='.modelweaver' \
  --exclude='docker-backup' \
  --exclude='__pycache__' \
  --exclude='node_modules' \
  -C "$SCRIPT_DIR" . | docker cp - "$CONTAINER_NAME:/app"

echo "⚙️  Configuration du mode $(mode_value "$MODE")..."
echo "$(mode_value "$MODE")" > /tmp/.modelweaver_config_test
docker cp /tmp/.modelweaver_config_test "$CONTAINER_NAME:/app/.modelweaver_config"
rm -f /tmp/.modelweaver_config_test

echo "▶️  Exécution..."
EXIT_CODE=0
docker start -a "$CONTAINER_NAME" || EXIT_CODE=$?

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# En mode check, on s'attend à ce que ça échoue (détection de manques)
# En mode auto/ask, on s'attend à un succès
if [ "$MODE" = "check" ]; then
    if [ $EXIT_CODE -ne 0 ]; then
        echo "✅ Check OK : modelweaver a correctement détecté les manques."
    else
        echo "⚠️  Check : modelweaver n'a rien signalé (peut-être que tout est déjà installé ?)."
    fi
else
    if [ $EXIT_CODE -eq 0 ]; then
        echo "✅ Installation réussie !"
    else
        echo "❌ Installation échouée (code $EXIT_CODE)."
        exit $EXIT_CODE
    fi
fi
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"

# --- Snapshot si demandé ---
if [ "$SAVE_ON_SUCCESS" = true ] && [ "$MODE" = "auto" ]; then
    NEXT_INDEX=$((INDEX + 1))
    NEXT_LEVEL=$(python3 -c "
import json
with open('$LEVELS_FILE') as f:
    for l in json.load(f)['levels']:
        if l['index'] == $NEXT_INDEX:
            print(l['id'])
" 2>/dev/null || echo "")

    SNAP_TAG="modelweaver-bak:${NEXT_LEVEL}"
    SNAP_TAR="$BACKUP_DIR/modelweaver-$(printf '%03d' "$NEXT_INDEX")-${NEXT_LEVEL}.tar"

    echo "📸 Snapshot → $NEXT_LEVEL"
    docker commit "$CONTAINER_NAME" "$SNAP_TAG" >/dev/null
    mkdir -p "$BACKUP_DIR"
    docker save "$SNAP_TAG" -o "$SNAP_TAR"
    echo "✅ Snapshot sauvegardé dans $SNAP_TAR"
fi

echo "✅ Terminé."
