#!/usr/bin/env python3
"""Installation complète dans Docker — utilise le nouvel Installer.

Usage:
    CATALOGUE_URL=http://host.docker.internal:8765/api python3 install_in_docker.py

Variables d'environnement :
    CATALOGUE_URL   — URL du serveur catalogue (obligatoire)
    MW_CACHE_DIR    — cache des téléchargements (défaut: /app/.modelweaver/cache)
"""

import os
import sys
import json
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
        print("❌ CATALOGUE_URL non définie.")
        sys.exit(1)

    print(f"🚀 Installation Docker (SQLite)")
    print(f"   Cache     : {cache_dir}")
    print(f"   Catalogue : {catalogue_url}")

    installer = Installer(cache_dir=cache_dir)

    # ── 1. Synchroniser le catalogue ──
    print("🌐 Synchro catalogue...")
    cat_db = CatalogueDB()
    results = cat_db.sync_from_url(catalogue_url)
    cat_db.close()

    # ── 2. Initialiser la BDD locale ──
    print("🗄️  BDD locale...")
    local_db = ModelWeaverDB()
    n = local_db.scan_installed_tools()
    print(f"   → {n} outils installés détectés")

    # ── 3. Importer les clés ──
    env_path = app_dir / ".env"
    if env_path.exists():
        print("🔑 Import des clés...")
        km = KeyManager(db=local_db)
        obo = Onboarder(km)
        nk = obo.onboard_from_env(env_path)
        print(f"   → {nk} clés importées")

    # ── 4. Installer les outils du catalogue ──
    print("📦 Installation des outils catalogue...")
    tools = local_db.tools.list_all()
    for t in tools:
        params_raw = t.get("installer_params")
        if isinstance(params_raw, str):
            try:
                t["installer_params"] = json.loads(params_raw)
            except json.JSONDecodeError:
                t["installer_params"] = {}
        installer.install(t)

    print("\n   Résumé BDD locale :")
    for table in ["providers", "models", "provider_models", "api_keys", "tools", "local_tools", "commands"]:
        c = local_db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"     {table:20s} → {c}")
    local_db.close()

    print("✅ Installation terminée.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
