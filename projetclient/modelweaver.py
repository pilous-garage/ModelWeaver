#!/usr/bin/env python3
"""ModelWeaver — Orchestrateur IA.

Usage séquentiel :
    1. BDD    → initialise les tables, fetch le catalogue
    2. Check  → détecte les outils installés
    3. Install → propose d'installer les outils manquants

Usage :
    python3 modelweaver.py [--cache=/chemin/vers/cache]
"""

import argparse
import json
import os
import sys
import time
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent


def _default_cache() -> Path:
    return _project_root() / ".modelweaver" / "cache"


def main():
    parser = argparse.ArgumentParser(description="ModelWeaver")
    parser.add_argument("--cache", type=str, default=str(_default_cache()),
                        help="Chemin du cache (défaut: .modelweaver/cache/)")
    args = parser.parse_args()

    cache_dir = Path(args.cache)
    cache_dir.mkdir(parents=True, exist_ok=True)

    print(f"🧠 ModelWeaver — Cache: {cache_dir}")
    print()

    # Ajouter les modules au path
    root = _project_root()
    sys.path.insert(0, str(root))

    # ── 1. BDD ──
    print("=" * 50)
    print("  Phase 1/3 : Base de données")
    print("=" * 50)
    from sql.db import ModelWeaverDB, CatalogueDB

    # La BDD locale se trouve dans le même dossier .modelweaver/ que le cache
    db_dir = cache_dir.parent  # .modelweaver/
    db_path = db_dir / "modelweaver.db"
    cat_path = db_dir / "catalogue.db"
    db = ModelWeaverDB(db_path=db_path)
    print(f"  ✅ BDD locale : {db.db_path}")
    print(f"     {len(db.providers.list_all())} providers")
    print(f"     {len(db.models.list_all())} modèles")
    print(f"     {len(db.tools.list_all())} outils (catalogue)")
    print(f"     {len(db.commands.list_all())} commandes")

    # Sync catalogue depuis le serveur local si dispo
    cat_url = os.environ.get("CATALOGUE_URL", "http://localhost:8764/api")
    try:
        import urllib.request
        from urllib.parse import urljoin
        req = urllib.request.Request(urljoin(cat_url, "/health"),
                                     headers={"User-Agent": "ModelWeaver"})
        with urllib.request.urlopen(req, timeout=3) as resp:
            if resp.status == 200:
                print(f"  🌐 Synchronisation depuis {cat_url}...")
                cat = CatalogueDB(db_path=cat_path)
                results = cat.sync_from_url(cat_url)
                cat.close()
                for table, count in results.items():
                    label = f"     → {table}"
                    print(f"{label:30s} {count}" if count >= 0 else f"{label:30s} ❌")
    except Exception:
        print(f"  ⚠️  Catalogue distant indisponible ({cat_url})")
        print("     Utilisation des données locales uniquement.")
    print()

    # ── 2. Check ──
    print("=" * 50)
    print("  Phase 2/3 : Scan des outils installés")
    print("=" * 50)

    n = db.scan_installed_tools()
    print(f"  🔍 {n} outils détectés sur le système")
    print()

    local_tools = db.local_tools.list_all()
    if local_tools:
        print(f"  {'Outil':20s} {'Version':15s} {'Statut':12s}  Chemin")
        print(f"  {'─'*20} {'─'*15} {'─'*12}  ──────────────────")
        for lt in local_tools:
            print(f"  {lt.get('tool_name','?'):20s} {lt.get('version','?'):15s} {lt.get('status','?'):12s} {lt.get('install_path','')}")
    print()

    # ── 3. Install ──
    print("=" * 50)
    print("  Phase 3/3 : Installation des outils")
    print("=" * 50)

    from modules.installer.installer import Installer
    installer = Installer(cache_dir=cache_dir)

    # Lister les outils catalogue non encore installés
    all_tools = db.tools.list_all()
    installed_refs = {lt["tool_ref"] for lt in local_tools}
    missing = [t for t in all_tools if t["ref"] not in installed_refs]

    if missing:
        print(f"  {len(missing)} outil(s) non installé(s) :")
        for t in missing:
            inst_method = t.get("install_method", "?")
            desc = t.get("description") or ""
            print(f"    [{inst_method:20s}] {t['ref']:12s} {desc}")
        print()
        print("  Voulez-vous installer ces outils ?")
        print("  [Y] Oui (tout installer)   [n] Non   [q] Quitter")
        try:
            choice = input("> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            choice = "n"
        if choice in ("y", "oui", "yes", ""):
            for t in missing:
                params_raw = t.get("installer_params")
                if isinstance(params_raw, str):
                    try:
                        t["installer_params"] = json.loads(params_raw)
                    except json.JSONDecodeError:
                        t["installer_params"] = {}
                ok = installer.install(t)
                if ok:
                    # Mettre à jour local_tools
                    tool_row = db.conn.execute(
                        "SELECT id FROM tools WHERE ref = ?", (t["ref"],)
                    ).fetchone()
                    if tool_row:
                        db.local_tools.save({
                            "tool_id": tool_row["id"],
                            "version": t.get("current_version", "?"),
                            "install_path": str(cache_dir),
                            "status": "installed",
                        })
            db.commit()
        elif choice == "q":
            print("  Arrêt.")
            db.close()
            return
        else:
            print("  Installation ignorée.")
    else:
        print("  ✅ Tous les outils du catalogue sont déjà installés.")
    print()

    # ── Résumé final ──
    print("=" * 50)
    print("  Résumé final")
    print("=" * 50)
    for table in ["providers", "models", "provider_models", "api_keys",
                   "tools", "local_tools", "commands"]:
        c = db.conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        print(f"  {table:20s} → {c}")
    print()

    db.close()
    print("✅ ModelWeaver terminé.")


if __name__ == "__main__":
    main()
