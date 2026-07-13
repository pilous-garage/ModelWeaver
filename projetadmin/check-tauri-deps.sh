#!/bin/bash

# Couleurs
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
BLUE='\033[1;34m'
NC='\033[0m'

log_ok() { echo -e "${GREEN}✅ $1${NC}"; }
log_err() { echo -e "${RED}❌ $1${NC}"; }
log_warn() { echo -e "${YELLOW}⚠️  $1${NC}"; }
log_info() { echo -e "${BLUE}[INFO]${NC} $1"; }

echo "===================================================="
echo "   Vérification des dépendances de build Tauri"
echo "===================================================="

MISSING_PKGS=()

# 1. Vérification des outils CLI
check_tool() {
    if command -v "$1" >/dev/null 2>&1; then
        log_ok "$1 est installé"
    else
        log_err "$1 est manquant"
        MISSING_PKGS+=("$1")
    fi
}

log_info "Vérification des outils de développement..."
check_tool "cargo"
check_tool "npm"
check_tool "git"
check_tool "curl"

echo ""

# 2. Vérification des bibliothèques système (Debian/Ubuntu)
# On vérifie via dpkg -s si le paquet est installé
check_pkg() {
    if dpkg -s "$1" >/dev/null 2>&1; then
        log_ok "$1 est installé"
    else
        log_err "$1 est manquant"
        MISSING_PKGS+=("$1")
    fi
}

log_info "Vérification des bibliothèques système (Linux)..."
# Liste des dépendances minimales pour Tauri 2.0 sur Linux
SYSTEM_DEPS=(
    "build-essential"
    "curl"
    "wget"
    "file"
    "libssl-dev"
    "libgtk-3-dev"
    "libwebkit2gtk-4.1-dev"
    "librsvg2-dev"
    "cmake"
    "pkg-config"
)

for pkg in "${SYSTEM_DEPS[@]}"; do
    check_pkg "$pkg"
done

echo ""
echo "===================================================="

if [ ${#MISSING_PKGS[@]} -eq 0 ]; then
    log_ok "Tout est prêt ! Vous pouvez compiler la GUI."
else
    log_warn "${#MISSING_PKGS[@]} dépendance(s) manquante(s)."
    echo -e "\nPour tout installer d'un coup, lancez la commande suivante :\n"
    echo -e "${BLUE}sudo apt-get update && sudo apt-get install -y ${MISSING_PKGS[*]}${NC}"
    echo ""
fi
echo "===================================================="
