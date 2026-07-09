#!/bin/sh
# ModelWeaver — Bootstrap minimal
# Usage: ./modelweaver.sh [--cache=/chemin/vers/cache]

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="/bin/modelweaver"
CACHE_ARG=""

# ─── Bootstrap : Téléchargement du projet ────────────────
if [ ! -f "$APP_DIR/modelweaver.py" ]; then
    echo "📦 ModelWeaver non détecté localement. Téléchargement depuis GitHub..."
    
    ARCHIVE_URL="https://github.com/pilous-garage/ModelWeaver/releases/latest/download/modelweaver_client.tar.gz"
    TMP_ARCHIVE="/tmp/modelweaver_client.tar.gz"
    
    if ! command -v curl >/dev/null 2>&1; then
        echo "❌ curl est requis pour le téléchargement. Veuillez l'installer."
        exit 1
    fi
    
    curl -L "$ARCHIVE_URL" -o "$TMP_ARCHIVE"
    
    echo "📦 Extraction du projet..."
    mkdir -p "$APP_DIR"
    tar -xzf "$TMP_ARCHIVE" -C "$APP_DIR"
    
    rm "$TMP_ARCHIVE"
    echo "✅ Projet récupéré avec succès."
fi
for arg in "$@"; do
    case "$arg" in
        --cache=*) CACHE_ARG="${arg#--cache=}" ;;
        --auto_install) AUTO_INSTALL=true ;;
        --help|-h)
            echo "Usage: $0 [--cache=/chemin/vers/cache] [--auto_install]"
            exit 0
            ;;
    esac
done

# Cache par défaut : dossier .modelweaver/cache/ du projet si --cache non fourni
if [ -z "$CACHE_ARG" ]; then
    CACHE_DIR="$APP_DIR/.modelweaver/cache"
else
    CACHE_DIR="$CACHE_ARG"
fi
mkdir -p "$CACHE_DIR"

# ─── Vérification Python 3 ──────────────────────
if ! command -v python3 >/dev/null 2>&1; then
    echo ""
    echo "⚠️  Python 3 n'est pas installé."
    echo "   ModelWeaver a besoin de Python 3.10+ pour fonctionner."
    echo "   Le programme va tenter de l'installer."
    echo "   (annulez avec Ctrl+C si vous préférez le faire vous-même)"
    echo ""
    sleep 2
    if command -v apt >/dev/null 2>&1; then
        apt update -qq && apt install -y -qq python3 python3-pip python3-venv python3-yaml
    elif command -v brew >/dev/null 2>&1; then
        brew install python3
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm python python-pip
    else
        echo "❌ Aucun gestionnaire de paquets trouvé."
        echo "   Installez Python 3.10+ manuellement puis relancez."
        exit 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ Échec de l'installation de Python."
        exit 1
    fi
    echo "✅ Python 3 installé."
fi

# ─── Vérification sqlite3 ───────────────────────
if ! python3 -c "import sqlite3" 2>/dev/null; then
    echo ""
    echo "⚠️  Le module sqlite3 n'est pas disponible dans Python."
    echo "   ModelWeaver a besoin de sqlite3 pour stocker ses données."
    echo "   Le programme va tenter de l'installer."
    echo ""
    sleep 2
    if command -v apt >/dev/null 2>&1; then
        apt install -y -qq python3-pip python3-yaml  # sqlite3 vient avec python3 sur Ubuntu
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3-libs
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm python
    else
        echo "❌ sqlite3 introuvable. Installez python3-sqlite3 manuellement."
        exit 1
    fi
    if ! python3 -c "import sqlite3" 2>/dev/null; then
        echo "❌ Échec de l'installation de sqlite3."
        exit 1
    fi
    echo "✅ sqlite3 disponible."
fi

# ─── Installation système ModelWeaver ──────────
if [ -d "$BIN_DIR" ]; then
    echo ""
    echo "✅ ModelWeaver est déjà installé dans $BIN_DIR"
    echo "   [K]  Utiliser la version existante  (par défaut)"
    echo "   [R]  Réinitialiser (recopier les fichiers depuis le projet)"
    printf "> "
    read CHOICE </dev/tty 2>/dev/null || CHOICE="k"
    case "$CHOICE" in
        r|R) rm -rf "$BIN_DIR" && mkdir -p "$BIN_DIR" ;;
        *)   echo "   → Version existante conservée" ;;
    esac
fi

if [ ! -d "$BIN_DIR" ]; then
    echo "📂 Installation de ModelWeaver dans $BIN_DIR..."
    mkdir -p "$BIN_DIR"

    # Copie des scripts nécessaires
    cp "$APP_DIR/modelweaver.sh" "$BIN_DIR/modelweaver.sh"
    cp "$APP_DIR/modelweaver.py" "$BIN_DIR/modelweaver.py" 2>/dev/null || true
    cp -r "$APP_DIR/sql" "$BIN_DIR/sql" 2>/dev/null || true
    cp -r "$APP_DIR/modules" "$BIN_DIR/modules" 2>/dev/null || true
    cp "$APP_DIR/manifest.json" "$BIN_DIR/manifest.json" 2>/dev/null || true
    cp -r "$APP_DIR/bin" "$BIN_DIR/" 2>/dev/null || true

    chmod +x "$BIN_DIR/modelweaver.sh"
    if [ -f "$BIN_DIR/modelweaver.py" ]; then
        chmod +x "$BIN_DIR/modelweaver.py"
    fi

    echo "✅ ModelWeaver installé dans $BIN_DIR"
fi

# ─── Lancement ────────────────────────────────────
echo ""
if [ "$AUTO_INSTALL" = true ]; then
    echo "🤖 Mode Auto-Install activé..."
    echo "   Installation des outils légers (curl, git, gitingest)..."
    echo ""
    
    export PYTHONPATH="$BIN_DIR"
    echo "   Préparation des dépendances Python..."
    python3 -m pip install -q pyyaml libsql-client python-dotenv psutil requests --break-system-packages || true
    python3 "$BIN_DIR/bin/mw_install.py" curl git gitingest opencode litellm
    
    echo ""
    echo "✅ Auto-installation terminée."
else
    echo "🚀 Lancement de ModelWeaver..."
    echo "   Cache : $CACHE_DIR"
    echo ""
    python3 "$BIN_DIR/modelweaver.py" --cache="$CACHE_DIR"
fi
