#!/usr/bin/env python3
"""Installation complète dans Docker — utilise la nouvelle implémentation SQLite.

Usage:
    CATALOGUE_URL=http://host.docker.internal:8765/api python3 install_in_docker.py

Variables d'environnement :
    CATALOGUE_URL   — URL du serveur catalogue (obligatoire)
    MW_CACHE_DIR    — cache des téléchargements (défaut: /app/.modelweaver/cache)
"""

import os
import sys
import subprocess
from pathlib import Path

sys.path.insert(0, "/app")

from modules.installer.installer import Installer
from modules.key_manager.key_manager import KeyManager
from modules.key_manager.onboarder import Onboarder
from sql.db import ModelWeaverDB, CatalogueDB


def main():
    app_dir = Path("/app")
    cache_dir = Path(os.environ.get("MW_CACHE_DIR", app_dir / ".modelweaver" / "cache"))
    catalogue_url = os.environ.get("CATALOGUE_URL", "")

    if not catalogue_url:
        print("❌ CATALOGUE_URL non définie. Usage: CATALOGUE_URL=http://... python3 install_in_docker.py")
        sys.exit(1)

    print(f"🚀 Installation Docker (SQLite)")
    print(f"   Cache     : {cache_dir}")
    print(f"   Catalogue : {catalogue_url}")
    print()

    # ── 1. Installer les dépendances système ──
    installer = Installer(cache_dir=cache_dir)
    deps = ["curl", "git", "python3-requests", "python3-yaml"]
    print("📦 Dépendances système...")
    installer.install_dependencies(deps)

    # ── 2. Créer venv + installer paquets Python ──
    venv_dir = app_dir / ".venv"
    if not venv_dir.exists():
        subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    venv_pip = str(venv_dir / "bin" / "pip")

    pkgs = ["litellm", "gitingest", "pyyaml", "requests"]
    for pkg in pkgs:
        subprocess.run([venv_pip, "install", pkg], check=True, capture_output=True)

    # ── 3. Initialiser la BDD locale ──
    print("🗄️  Initialisation de la BDD locale...")
    local_db = ModelWeaverDB()

    # Scan des outils installés
    n = local_db.scan_installed_tools()
    print(f"   → {n} outils installés détectés")

    # ── 4. Synchroniser le catalogue ──
    print(f"🌐 Synchronisation du catalogue depuis {catalogue_url}...")
    cat_db = CatalogueDB()
    results = cat_db.sync_from_url(catalogue_url)
    for table, count in results.items():
        print(f"   → {table}: {count}")
    cat_db.close()

    # ── 5. Importer les clés ──
    env_path = app_dir / ".env"
    if env_path.exists():
        print("🔑 Import des clés depuis .env...")
        km = KeyManager(db=local_db)
        obo = Onboarder(km)
        n = obo.onboard_from_env(env_path)
        print(f"   → {n} clés importées")
    else:
        print("⚠️  Aucun .env trouvé, pas de clés importées")

    print("\n   Résumé BDD locale :")
    for table in ["providers", "models", "provider_models", "api_keys", "tools", "local_tools", "commands"]:
        c = local_db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"     {table:20s} → {c}")
    local_db.close()

    print()
    print("✅ Installation terminée. La BDD est prête.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
