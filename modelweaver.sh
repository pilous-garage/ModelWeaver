#!/bin/sh
# Bootstrap ModelWeaver — lance modelweaver.py avec un Python disponible

APP_DIR="$(cd "$(dirname "$0")" && pwd)"
MODE="$(cat "$APP_DIR/.modelweaver_config" 2>/dev/null || echo 'YES')"

# Vérifie que python3 existe, sinon l'installe
if ! command -v python3 >/dev/null 2>&1; then
    if [ "$MODE" = "NO" ]; then
        echo "❌ Python 3 requis mais introuvable, et mode check actif. Arrêt."
        exit 1
    fi
    echo "📦 Installation de Python 3..."
    if command -v apt >/dev/null 2>&1; then
        apt update -qq && apt install -y -qq -o APT::Keep-Downloaded-Packages=true python3
    elif command -v brew >/dev/null 2>&1; then
        brew install python3
    elif command -v dnf >/dev/null 2>&1; then
        dnf install -y python3 python3-pip
    elif command -v pacman >/dev/null 2>&1; then
        pacman -Sy --noconfirm python python-pip
    else
        echo "❌ Aucun gestionnaire de paquets. Installe Python 3 manuellement."
        exit 1
    fi
    if ! command -v python3 >/dev/null 2>&1; then
        echo "❌ Échec de l'installation de Python."
        exit 1
    fi
fi

# Vérifie la version de Python ; si < 3.9, upgrade vers 3.10+
PYTHON_BIN="python3"
PYTHON_VER=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
PYTHON_MAJ=$(echo "$PYTHON_VER" | cut -d. -f1)
PYTHON_MIN=$(echo "$PYTHON_VER" | cut -d. -f2)

if [ "$PYTHON_MAJ" -lt 3 ] || { [ "$PYTHON_MAJ" -eq 3 ] && [ "$PYTHON_MIN" -lt 10 ]; }; then
    echo "⚠️  Python $PYTHON_VER détecté, version 3.10+ requise."
    if [ "$MODE" = "NO" ]; then
        echo "❌ Mode check : mettez à jour Python manuellement."
        exit 1
    fi
    # Tentative d'installer Python 3.10 depuis deadsnakes PPA (Debian/Ubuntu)
    if command -v add-apt-repository >/dev/null 2>&1 || command -v apt >/dev/null 2>&1; then
        echo "📦 Installation de Python 3.10..."
        if ! command -v add-apt-repository >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get update -qq
            DEBIAN_FRONTEND=noninteractive apt-get install -y -qq software-properties-common python3-launchpadlib python3-apt
        fi
        DEBIAN_FRONTEND=noninteractive add-apt-repository -y ppa:deadsnakes/ppa 2>&1 || true
        DEBIAN_FRONTEND=noninteractive apt-get update 2>&1
        DEBIAN_FRONTEND=noninteractive apt-get install -y -qq python3.10 2>&1 || true
        if command -v python3.10 >/dev/null 2>&1; then
            PYTHON_BIN="python3.10"
            echo "✅ Python 3.10 installé (ppa:deadsnakes)"
        else
            echo "⚠️  PPA deadsnakes indisponible pour cette version d'OS."
            echo "📦 Téléchargement de Python 3.10 statique (python-build-standalone)..."
            PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20250115/cpython-3.10.16+20250115-x86_64-unknown-linux-gnu-install_only.tar.gz"
            PYTHON_DEST="/opt/python3.10-static"
            mkdir -p "$PYTHON_DEST"
            if command -v curl >/dev/null 2>&1; then
                curl -fsSL "$PYTHON_URL" -o /tmp/python3.10-static.tar.gz
            elif command -v wget >/dev/null 2>&1; then
                wget -q "$PYTHON_URL" -O /tmp/python3.10-static.tar.gz
            else
                DEBIAN_FRONTEND=noninteractive apt-get install -y -qq curl
                curl -fsSL "$PYTHON_URL" -o /tmp/python3.10-static.tar.gz
            fi
            tar xzf /tmp/python3.10-static.tar.gz -C "$PYTHON_DEST"
            rm -f /tmp/python3.10-static.tar.gz
            if [ -x "$PYTHON_DEST/python/bin/python3" ]; then
                ln -sf "$PYTHON_DEST/python/bin/python3" /usr/local/bin/python3.10
                ln -sf "$PYTHON_DEST/python/bin/pip3" /usr/local/bin/pip3 2>/dev/null || true
                PYTHON_BIN="$PYTHON_DEST/python/bin/python3"
                echo "✅ Python 3.10 statique installé dans $PYTHON_DEST"
            else
                echo "⚠️  Échec du téléchargement de Python 3.10 statique."
                echo "⚠️  Utilisation de Python $PYTHON_VER existant"
            fi
        fi
    elif command -v brew >/dev/null 2>&1; then
        brew install python@3.10 && PYTHON_BIN="python3.10"
    else
        echo "⚠️  Impossible d'upgrader Python automatiquement."
    fi
fi

# Installer zstd + pip3 si manquants
if command -v apt >/dev/null 2>&1; then
    if ! command -v zstd >/dev/null 2>&1; then
        apt install -y -qq zstd 2>/dev/null || true
    fi
    if ! command -v pip3 >/dev/null 2>&1; then
        apt install -y -qq python3-pip python3-venv 2>/dev/null || true
    fi
fi

echo "🚀 Lancement de ModelWeaver..."
exec "$PYTHON_BIN" "$APP_DIR/modelweaver.py" "$@"
