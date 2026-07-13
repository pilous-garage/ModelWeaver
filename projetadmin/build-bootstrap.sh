#!/bin/bash
set -e

# Configuration
APP1_DIR="gui-bootstrap"
APP2_DIR="gui-main"
APP1_BIN="modelweaver-bootstrap"
APP2_BIN="modelweaver"
BINARY_ONLY=false

# Parse arguments
for arg in "$@"; do
    case "$arg" in
        --binary-only) BINARY_ONLY=true ;;
        --auto-release) AUTO_RELEASE=true ;;
    esac
done

VERSION=$(grep '"version":' $APP1_DIR/package.json | cut -d '"' -f 4)
TAG="v$VERSION"

# Couleurs
GREEN='\033[0;32m'
BLUE='\033[1;34m'
RED='\033[0;31m'
NC='\033[0m'

log() { echo -e "${BLUE}[INFO]${NC} $1"; }
error() { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }

# 1. Vérification des outils
log "Vérification des outils..."
command -v npm >/dev/null 2>&1 || error "npm n'est pas installé"
command -v cargo >/dev/null 2>&1 || error "cargo n'est pas installé"
command -v python3 >/dev/null 2>&1 || error "python3 n'est pas installé"

# 2. Build des deux apps Tauri
build_app() {
    local dir=$1
    local name=$2

    log "Build de $name..."
    (cd $dir && npm install)
    # Always build frontend assets
    (cd $dir && npm run build)
    if [ "$BINARY_ONLY" = true ]; then
        log "Build binaire uniquement (release)..."
        (cd $dir/src-tauri && export PATH="$HOME/.cargo/bin:$PATH" && cargo build --release)
    else
        (cd $dir && export PATH="$HOME/.cargo/bin:$PATH" && npm run tauri build)
    fi

    # Localisation du binaire
    local bin_path=$(find $dir/src-tauri/target/release -maxdepth 1 -name "$name" -type f | head -n 1)
    if [ -z "$bin_path" ]; then
        error "Binaire $name non trouvé"
    fi
    log "✓ $name généré : $bin_path"

    # Copie vers target/release/ racine
    mkdir -p target/release
    cp "$bin_path" "target/release/$name"
}

log "Début du build (2 apps)..."
build_app "$APP1_DIR" "$APP1_BIN"
build_app "$APP2_DIR" "$APP2_BIN"

log "${GREEN}Les deux binaires sont prêts dans target/release/ :${NC}"
ls -lh target/release/$APP1_BIN target/release/$APP2_BIN

# 3. Gestion de la Release
if [[ "$AUTO_RELEASE" == true ]]; then
    log "Lancement de la release automatique vers GitHub..."

    if ! command -v gh >/dev/null 2>&1; then
        error "GitHub CLI (gh) n'est pas installé. Impossible de publier la release."
    fi

    gh release create "$TAG" \
        "target/release/$APP1_BIN" \
        "target/release/$APP2_BIN" \
        --title "ModelWeaver $TAG" \
        --notes "Build automatique.\n- $APP1_BIN : bootstrap installer\n- $APP2_BIN : application principale"

    log "${GREEN}Release $TAG publiée avec succès !${NC}"
else
    log "Build terminé. Pour publier, utilisez : ./build-bootstrap.sh --auto-release"
fi
