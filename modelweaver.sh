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

if [ "$PYTHON_MAJ" -lt 3 ] || { [ "$PYTHON_MAJ" -eq 3 ] && [ "$PYTHON_MIN" -lt 9 ]; }; then
    echo "⚠️  Python $PYTHON_VER détecté, version 3.9+ requise."
    if [ "$MODE" = "NO" ]; then
        echo "❌ Mode check : mettez à jour Python manuellement."
        exit 1
    fi
    # Tentative d'installer Python 3.10 depuis deadsnakes PPA (Debian/Ubuntu)
    if command -v add-apt-repository >/dev/null 2>&1 || command -v apt >/dev/null 2>&1; then
        echo "📦 Installation de Python 3.10..."
        if ! command -v add-apt-repository >/dev/null 2>&1; then
            apt update -qq && apt install -y -qq software-properties-common python3-launchpadlib python3-apt
        fi
        add-apt-repository -y ppa:deadsnakes/ppa 2>&1 | tail -10
        apt update -qq 2>&1 | tail -10
        apt install -y -qq python3.10 python3.10-distutils python3.10-venv 2>&1 | tail -5
        if command -v python3.10 >/dev/null 2>&1; then
            PYTHON_BIN="python3.10"
            echo "✅ Python 3.10 installé"
        else
            echo "⚠️  Échec de l'installation de Python 3.10, utilisation de Python $PYTHON_VER"
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
