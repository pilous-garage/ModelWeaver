#!/bin/sh
# ModelWeaver — Bootstrap minimal
# Usage: ./modelweaver.sh [--cache=/chemin/vers/cache]

set -e

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
BIN_DIR="/bin/modelweaver"
CACHE_ARG=""

# ─── Parse arguments ────────────────────────────
for arg in "$@"; do
    case "$arg" in
        --cache=*) CACHE_ARG="${arg#--cache=}" ;;
        --help|-h)
            echo "Usage: $0 [--cache=/chemin/vers/cache]"
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
        apt update -qq && apt install -y -qq python3 python3-pip python3-venv
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
        apt install -y -qq python3-pip  # sqlite3 vient avec python3 sur Ubuntu
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

    chmod +x "$BIN_DIR/modelweaver.sh"
    if [ -f "$BIN_DIR/modelweaver.py" ]; then
        chmod +x "$BIN_DIR/modelweaver.py"
    fi

    echo "✅ ModelWeaver installé dans $BIN_DIR"
fi

# ─── Lancer le cœur Python ─────────────────────
echo ""
echo "🚀 Lancement de ModelWeaver..."
echo "   Cache : $CACHE_DIR"
echo ""

python3 "$BIN_DIR/modelweaver.py" --cache="$CACHE_DIR"
