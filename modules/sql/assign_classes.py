"""Script de maintenance : (ré)assigne les classes métier aux outils.

Le seed automatique des classes (10 classes métier) et le backfill par défaut
sont désormais gérés dans db.py (_ensure_classes_outils_table +
_default_class_for_ref, exécutés par CatalogueDB._ensure_schema /
ModelWeaverDB._ensure_schema). Ce script sert à forcer une réassignation
manuelle (ex: après avoir corrigé le mapping) sur les deux bases.

Usage:
    python modules/sql/assign_classes.py
"""
import sys
from pathlib import Path

sys.path.append(str(Path(__file__).resolve().parent.parent))

from modules.sql.db import (
    CatalogueDB,
    ModelWeaverDB,
    _ensure_classes_outils_table,
    resolve_classe_id,
    _default_class_for_ref,
)


def assign_on(conn, table: str, ref_col: str, id_col: str) -> int:
    """Pour chaque ligne de `table` sans classe_outil_id, déduit via le ref."""
    _ensure_classes_outils_table(conn)
    updated = 0
    for row in conn.execute(
        f"SELECT {id_col}, {ref_col} FROM {table} WHERE classe_outil_id IS NULL"
    ).fetchall():
        cid = resolve_classe_id(conn, _default_class_for_ref(row[ref_col]))
        if cid is not None:
            conn.execute(
                f"UPDATE {table} SET classe_outil_id=? WHERE {id_col}=?",
                (cid, row[id_col]),
            )
            updated += 1
    conn.commit()
    return updated


def main():
    cat = CatalogueDB()
    n_cat = assign_on(cat.conn, "catalogue_outils", "ref", "outil_id")
    cat.close()

    mw = ModelWeaverDB()
    n_loc = assign_on(mw.conn, "local_outils", "outil_ref", "local_outil_id")
    mw.close()

    print(f"✅ Classes réassignées — catalogue: {n_cat}, local: {n_loc}")


if __name__ == "__main__":
    main()
