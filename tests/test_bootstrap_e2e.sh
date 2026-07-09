#!/bin/bash
set -e

echo "🚀 Lancement du test de bootstrap E2E..."

# 1. Préparation de l'image et du volume
# On crée un dossier temporaire pour simuler le dossier de l'utilisateur
TEST_DIR=$(mktemp -d)
echo "📂 Dossier de test : $TEST_DIR"

# On copie uniquement le script bootstrap dans ce dossier
cp projetclient/modelweaver.sh "$TEST_DIR/modelweaver.sh"
chmod +x "$TEST_DIR/modelweaver.sh"

# 2. Lancement du conteneur Docker (Ubuntu Bare)
# On monte le dossier de test et on injecte les variables d'env nécessaires
echo "🐳 Démarrage du conteneur Ubuntu bare..."
docker run --rm \
    -v "$TEST_DIR":/app \
    -w /app \
    -e TURSO_URL=$(grep TURSO_URL .env | cut -d '=' -f2 | tr -d '"') \
    -e TURSO_TOKEN=$(grep TURSO_TOKEN .env | cut -d '=' -f2 | tr -d '"') \
    ubuntu:24.04 bash -c "
        # Installation minimale pour que le script puisse commencer
        apt-get update && apt-get install -y curl &&
        
        echo '\n--- 1. Execution de modelweaver.sh --auto_install ---'
        ./modelweaver.sh --auto_install
        
        echo '\n--- 2. Vérifications ---'
        if [ -f /app/modelweaver.py ]; then
            echo '✅ modelweaver.py est présent'
        else
            echo '❌ modelweaver.py est MANQUANT'
            exit 1
        fi
        
        if [ -d /bin/modelweaver ]; then
            echo '✅ /bin/modelweaver a été créé'
        else
            echo '❌ /bin/modelweaver est MANQUANT'
            exit 1
        fi
        
        which curl >/dev/null && echo '✅ curl installé' || echo '❌ curl manquant'
        which git >/dev/null && echo '✅ git installé' || echo '❌ git manquant'
        python3 -m pip show gitingest >/dev/null && echo '✅ gitingest installé' || echo '❌ gitingest manquant'
    "

# Nettoyage
rm -rf "$TEST_DIR"
echo "✨ Test terminé."
