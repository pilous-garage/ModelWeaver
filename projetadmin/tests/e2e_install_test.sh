#!/bin/bash
set -e

echo "🚀 Lancement du conteneur de test optimisé..."
docker run --rm \
    -v $(pwd):/app \
    -w /app \
    -e TURSO_URL=$(grep TURSO_URL .env | cut -d '=' -f2 | tr -d '"') \
    -e TURSO_TOKEN=$(grep TURSO_TOKEN .env | cut -d '=' -f2 | tr -d '"') \
    modelweaver-test-base bash -c "
        echo '🧹 Nettoyage du cache local pour forcer le téléchargement GitHub...'
        rm -rf /app/install_recipe
        
        echo '\n--- 1. Execution de modelweaver.sh --auto_install ---'
        export PYTHONPATH=\$PYTHONPATH:/app
        ./modelweaver.sh --auto_install
        
        echo '\n--- 2. Vérification finale ---'
        which curl || echo 'Curl failed'
        which git || echo 'Git failed'
        python3 -m pip show gitingest || echo 'Gitingest failed'
    "

