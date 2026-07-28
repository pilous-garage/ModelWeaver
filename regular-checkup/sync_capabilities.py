"""Sync les capacités des modèles (function calling, vision, etc.).

Utilise la base de connaissance FAMILY_CAPABILITIES pour remplir
model_capabilities pour les modèles qui n'ont pas encore de source
renseignée.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.llm_manager.catalogue_sync import (
    sync_from_local, CAPABILITIES_SCHEMA,
)
from modules.sql.db import CatalogueDB


def main():
    db_path = Path.home() / ".modelweaver" / "catalogue.db"
    if not db_path.exists():
        print("Catalogue DB not found")
        return 1

    cat = CatalogueDB(str(db_path))
    cat.conn.execute(CAPABILITIES_SCHEMA)
    cat.conn.commit()

    # Sync from local knowledge base
    count = sync_from_local(cat)
    print(f"  {count} models updated from knowledge base")

    # Also add capabilities for models from key_endpoint_models
    # that don't have capabilities yet
    rows = cat.conn.execute("""
        SELECT DISTINCT m.ref
        FROM key_endpoint_models kem
        JOIN catalogue_models m ON m.id = kem.model_id
        LEFT JOIN model_capabilities mc ON mc.model_ref = m.ref
        WHERE mc.model_ref IS NULL AND kem.available = 1
    """).fetchall()

    extras = 0
    for row in rows:
        ref = row["ref"]
        cat.conn.execute("""
            INSERT OR IGNORE INTO model_capabilities
                (model_ref, supports_chat, source)
            VALUES (?, 1, 'default')
        """, (ref,))
        extras += 1

    cat.conn.commit()
    if extras:
        print(f"  {extras} additional models set to chat-capable (default)")
        count += extras

    print(f"Total: {count} models with capabilities")
    return 0


if __name__ == "__main__":
    sys.exit(main())
