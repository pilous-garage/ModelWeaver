"""Remplit catalogue_models.model_key (identifiant canonique par MODÈLE).

Usage :
    python3 scripts/fill_model_keys.py

Pour chaque modèle du catalogue, calcule le model_key (normalisation des
refs : préfixe provider, casse, suffixes de variante) et l'écrit en base.
Les modèles déjà remplis sont sautés (idempotent).
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sql.db import CatalogueDB


def main():
    spec = None
    for p in (REPO_ROOT / "scraper-base" / "benchmarks" / "model_key.py",):
        import importlib.util
        spec = importlib.util.spec_from_file_location("model_key", p)
        break
    if spec is None:
        print("ERREUR: model_key.py introuvable")
        return 1
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)

    cat = CatalogueDB()
    conn = cat.conn
    rows = conn.execute(
        "SELECT id, ref, model_key FROM catalogue_models ORDER BY id").fetchall()
    updated = 0
    skipped = 0
    for r in rows:
        key = mk.model_key(r["ref"])
        if not key:
            skipped += 1
            continue
        if r["model_key"] == key:
            skipped += 1
            continue
        conn.execute("UPDATE catalogue_models SET model_key = ? WHERE id = ?",
                     (key, r["id"]))
        updated += 1
    conn.commit()
    print(f"model_key mis à jour : {updated} modèles, {skipped} inchangés")

    # Statistiques de regroupement
    groups = conn.execute(
        "SELECT model_key, COUNT(*) n FROM catalogue_models WHERE model_key != '' "
        "GROUP BY model_key HAVING n > 1 ORDER BY n DESC LIMIT 12").fetchall()
    print("\nRegroupements (model_key → nb variantes) :")
    for g in groups:
        print(f"  {g['model_key'][:40]:40} ×{g['n']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
