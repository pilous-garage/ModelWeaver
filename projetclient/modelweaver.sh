#!/bin/bash

# ==============================================================================
# ModelWeaver Bootstrap Installer
# ==============================================================================

# Couleurs pour le terminal
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

log_info() { echo -e "${GREEN}[INFO]${NC} $1"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
log_err() { echo -e "${RED}[ERROR]${NC} $1"; }

# --- Configuration par défaut ---
DEFAULT_INSTALL_DIR="$HOME/.modelweaver"
PROJECT_NAME="ModelWeaver"

# --- Fonctions de vérification ---

check_python() {
    if command -v python3 >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

check_sqlite() {
    if command -v sqlite3 >/dev/null 2>&1; then
        return 0
    else
        return 1
    fi
}

# --- Logique d'installation ---

install_dependencies() {
    log_info "Installation des dépendances système (Python, SQLite...)"
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-pip sqlite3 curl git > /dev/null 2>&1
    
    log_info "Installation des bibliothèques Python..."
    python3 -m pip install -q pyyaml libsql-client python-dotenv psutil requests keyring --break-system-packages || true
}

download_project() {
    local target_dir=$1
    log_info "Téléchargement du projet depuis GitHub..."
    mkdir -p "$target_dir"
    # Ici, on simule ou on utilise gh release download
    git clone --depth 1 https://github.com/anomalyco/ModelWeaver.git "$target_dir" > /dev/null 2>&1
}

# --- Modes de lancement ---

run_interactive() {
    echo "===================================================="
    echo "   Welcome to $PROJECT_NAME Installer (Interactive)"
    echo "===================================================="

    # 1. Chemin d'installation
    read -p "Où souhaitez-vous installer $PROJECT_NAME ? [$DEFAULT_INSTALL_DIR]: " install_path
    install_path=${install_path:-$DEFAULT_INSTALL_DIR}
    
    # 2. CGU
    echo -e "\n--- Conditions Générales d'Utilisation ---"
    echo "Le logiciel est fourni 'tel quel'. L'utilisateur est responsable de ses clés API."
    read -p "Acceptez-vous les CGU ? (y/n): " accept_tos
    if [[ "$accept_tos" != "y" ]]; then
        log_err "Installation annulée. Vous devez accepter les CGU."
        exit 1
    fi

    # 3. Dépendances
    if ! check_python || ! check_sqlite; then
        read -p "Certaines dépendances sont manquantes. Les installer maintenant ? (y/n): " install_deps
        if [[ "$install_deps" == "y" ]]; then
            install_dependencies
        else
            log_err "L'installation nécessite Python et SQLite. Arrêt."
            exit 1
        fi
    fi

    # 4. Téléchargement
    read -p "Télécharger le moteur ModelWeaver ? (y/n): " do_download
    if [[ "$do_download" == "y" ]]; then
        download_project "$install_path"
    fi

    log_info "Installation terminée avec succès dans $install_path !"
}

run_autoinstall() {
    log_info "Lancement de l'installation automatique..."
    
    install_dependencies
    download_project "$DEFAULT_INSTALL_DIR"
    
    log_info "Installation automatique terminée dans $DEFAULT_INSTALL_DIR !"
}

# --- Entrée principale ---

if [[ "$1" == "--autoinstall" ]]; then
    run_autoinstall
else
    run_interactive
fi
