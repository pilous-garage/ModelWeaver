#!/bin/bash

# ==============================================================================
# ModelWeaver Bootstrap Installer (Linux/macOS)
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
    
    # Détection de l'OS pour le gestionnaire de paquets
    if [[ "$OSTYPE" == "linux-gnu"* ]]; then
        if command -v apt-get >/dev/null 2>&1; then
            apt-get update -qq
            apt-get install -y -qq python3 python3-pip sqlite3 curl git > /dev/null 2>&1
        elif command -v brew >/dev/null 2>&1; then
            brew install python3 sqlite curl git > /dev/null 2>&1
        else
            log_err "Gestionnaire de paquets non supporté. Veuillez installer python3 et sqlite3 manuellement."
            exit 1
        fi
    elif [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew >/dev/null 2>&1; then
            brew install python3 sqlite curl git > /dev/null 2>&1
        else
            log_err "Homebrew n'est pas installé. Veuillez l'installer pour continuer."
            exit 1
        fi
    fi
    
    log_info "Installation des bibliothèques Python..."
    # Utilisation de pip3 explicitement
    pip3 install -q pyyaml libsql-client python-dotenv psutil requests keyring --break-system-packages || pip3 install -q pyyaml libsql-client python-dotenv psutil requests keyring
}

download_project() {
    local target_dir=$1
    log_info "Téléchargement du projet depuis GitHub..."
    mkdir -p "$target_dir"
    # On utilise git clone pour la simplicité du bootstrap
    git clone --depth 1 https://github.com/pilous-garage/ModelWeaver.git "$target_dir" > /dev/null 2>&1
}

# --- Modes de lancement ---

run_interactive() {
    echo "===================================================="
    echo "   Welcome to $PROJECT_NAME Installer (Interactive)"
    echo "===================================================="

    read -p "Où souhaitez-vous installer $PROJECT_NAME ? [$DEFAULT_INSTALL_DIR]: " install_path
    install_path=${install_path:-$DEFAULT_INSTALL_DIR}

    echo -e "\n--- Conditions Générales d'Utilisation ---"
    echo "Le logiciel est fourni 'tel quel'. L'utilisateur est responsable de ses clés API."
    read -p "Acceptez-vous les CGU ? (y/n): " accept_tos
    if [[ "$accept_tos" != "y" ]]; then
        log_err "Installation annulée. Vous devez accepter les CGU."
        exit 1
    fi

    if ! check_python || ! check_sqlite; then
        read -p "Certaines dépendances sont manquantes. Les installer maintenant ? (y/n): " install_deps
        if [[ "$install_deps" == "y" ]]; then
            install_dependencies
        else
            log_err "L'installation nécessite Python et SQLite. Arrêt."
            exit 1
        fi
    fi

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
