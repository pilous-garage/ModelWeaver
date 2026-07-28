"""Sync depuis les API des providers.

Pour chaque provider configuré (via .env ou keyring), interroge
``/v1/models`` et peuple les tables :
  - catalogue_models
  - key_endpoint_models
  - model_capabilities

Utilise DirectBridge pour la découverte.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.llm_manager.direct_bridge import DirectBridge
from modules.sql.db import CatalogueDB
from modules.llm_manager.catalogue_sync import CAPABILITIES_SCHEMA, _match_family
from pathlib import Path as P


def main():
    db_path = P.home() / ".modelweaver" / "catalogue.db"
    if not db_path.exists():
        print("Catalogue DB not found")
        return 1

    cat = CatalogueDB(str(db_path))
    cat.conn.execute(CAPABILITIES_SCHEMA)
    cat.conn.commit()

    bridge = DirectBridge(cat=cat)
    providers = ["nvidia", "groq", "openai", "google", "mistral", "ollama",
                 "openrouter", "huggingface"]

    total = 0
    for pref in providers:
        try:
            models = bridge.list_available_models(pref)
        except Exception as e:
            print(f"  [{pref}] ERROR: {e}")
            continue

        if not models:
            print(f"  [{pref}] 0 models (skip)")
            continue

        # upsert models
        count = 0
        for m in models:
            mref = m["ref"]
            name = m.get("name", mref)
            cat.conn.execute(
                "INSERT OR IGNORE INTO catalogue_models (ref, name) VALUES (?, ?)",
                (mref, name),
            )

            # upsert capabilities via family knowledge
            caps = _match_family(mref)
            if caps:
                cat.conn.execute("""
                    INSERT INTO model_capabilities
                        (model_ref, supports_chat, supports_function_calling,
                         supports_vision, supports_streaming, source)
                    VALUES (?, ?, ?, ?, ?, 'knowledge')
                    ON CONFLICT(model_ref) DO UPDATE SET
                        source = CASE WHEN model_capabilities.source = 'unknown'
                            THEN 'knowledge' ELSE model_capabilities.source END
                """, (mref,
                      caps.get("chat", caps.get("supports_chat", 0)),
                      caps.get("fc", caps.get("supports_function_calling", 0)),
                      caps.get("vision", caps.get("supports_vision", 0)),
                      caps.get("streaming", caps.get("supports_streaming", 1)),
                ))

            # upsert key_endpoint_models
            cat.conn.execute("""
                INSERT OR IGNORE INTO key_endpoint_models
                    (provider_id, model_id, provider_model_name, declared, available, last_checked_at)
                VALUES (
                    (SELECT id FROM catalogue_providers WHERE ref = ?),
                    (SELECT id FROM catalogue_models WHERE ref = ?),
                    ?, 1, 1, strftime('%s','now')
                )
            """, (pref, mref, mref))

            count += 1

        cat.conn.commit()
        print(f"  [{pref}] {count} models synced")
        total += count

    print(f"Total: {total} models from {len(providers)} providers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
