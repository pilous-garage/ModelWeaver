import os
from pathlib import Path
from dotenv import load_dotenv
from modules.sql.db import TursoCatalogueDB, CatalogueDB

load_dotenv()


def sync_local_to_remote():
    print("🚀 Syncing local catalogue to Turso...")

    url = os.getenv("TURSO_URL")
    token = os.getenv("TURSO_TOKEN")
    if not url or not token:
        print("❌ TURSO_URL / TURSO_TOKEN manquants dans .env")
        return

    try:
        remote = TursoCatalogueDB()
    except Exception as e:
        print(f"❌ Connexion Turso impossible: {e}")
        return

    try:
        cat = CatalogueDB()
    except Exception as e:
        print(f"❌ Catalogue local impossible: {e}")
        return

    tools = cat.conn.execute("SELECT * FROM catalogue_outils").fetchall()
    print(f"📦 {len(tools)} outils locaux")

    for tool in tools:
        d = dict(tool)
        ok = remote.upsert_tool(d)
        if not ok:
            print(f"  ⚠️  upsert_tool échoué: {d.get('ref')}")
            continue

        # Récupérer le outil_id distant (auto-généré)
        cur = remote.client.execute(
            "SELECT outil_id FROM catalogue_outils WHERE ref=?", (d["ref"],))
        row = cur.fetchone()
        if not row:
            print(f"  ⚠️  impossible de trouver {d['ref']} sur le remote")
            continue
        remote_outil_id = row[0]

        # Versions
        versions = cat.conn.execute(
            "SELECT * FROM catalogue_versions WHERE outil_id=?", (d["outil_id"],)
        ).fetchall()
        for ver in versions:
            v = dict(ver)
            rid = remote.upsert_version(remote_outil_id, v["nom_version"], v.get("description"))
            if rid is None:
                print(f"  ⚠️  upsert_version échoué: {d['ref']} v{v['nom_version']}")
                continue

            # Recettes
            recettes = cat.conn.execute(
                "SELECT * FROM catalogue_recettes WHERE version_id=?", (v["version_id"],)
            ).fetchall()
            for rec in recettes:
                r = dict(rec)
                ok2 = remote.upsert_recipe(
                    rid, r["os"], r["arch"], r["manager"],
                    package=r.get("package"), content=r.get("content"),
                    confidence=r.get("confidence", 1.0))
                if not ok2:
                    print(f"  ⚠️  upsert_recipe échoué: {d['ref']} {r['manager']}({r['os']}/{r['arch']})")

        # Popularité
        pop = cat.conn.execute(
            "SELECT nb_install, nb_desinstall FROM outils_popularite WHERE outil_id=?",
            (d["outil_id"],)).fetchone()
        if pop:
            remote.upsert_popularity(remote_outil_id, pop["nb_install"], pop["nb_desinstall"])

        print(f"  ✅ {d.get('ref')}")

    cat.close()
    print("✅ Sync terminée")


if __name__ == "__main__":
    sync_local_to_remote()
