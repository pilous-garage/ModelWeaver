#!/usr/bin/env python3
"""Migration JSON → SQLite pour ModelWeaver.

Lit les fichiers JSON existants et peuple les bases SQLite.
Ne supprime PAS les fichiers JSON originaux.
Les bases existantes sont backupées avant d'être réécrites.
"""

import json
import sqlite3
import uuid
import time
import shutil
from pathlib import Path
from typing import Any, Dict, Optional

BASE_DIR = Path(__file__).resolve().parent.parent
MODELWEAVER_DIR = BASE_DIR / ".modelweaver"
SQL_DIR = BASE_DIR / "sql"
CATALOGUE_DATA_DIR = BASE_DIR / "modules" / "catalogue" / "data"


def ref(prefix: str = "key") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def load_json(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default if default is not None else {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError) as e:
        print(f"  ⚠️  Erreur lecture {path.name}: {e}")
        return default if default is not None else {}


def execute_schema(conn: sqlite3.Connection, schema_path: Path) -> None:
    with open(schema_path, "r", encoding="utf-8") as f:
        conn.executescript(f.read())


def get_or_create_provider(cur: sqlite3.Cursor, ref_id: str, name: str = None,
                           ptype: str = "cloud") -> int:
    cur.execute("SELECT id FROM providers WHERE ref = ?", (ref_id,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("""
        INSERT INTO providers (ref, name, provider_type, catalogue_ref)
        VALUES (?, ?, ?, ?)
    """, (ref_id, name or ref_id, ptype, ref_id))
    return cur.lastrowid


def migrate_local_db(db_path: Path) -> None:
    print(f"\n  ── BDD locale ──")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    execute_schema(conn, SQL_DIR / "modelweaver_schema.sql")
    cur = conn.cursor()

    # ── 1. Providers ──
    providers_data = load_json(CATALOGUE_DATA_DIR / "providers.json", [])
    print(f"  → Fournisseurs : {len(providers_data)}")
    for p in providers_data:
        cur.execute("""
            INSERT OR IGNORE INTO providers (ref, name, provider_type, catalogue_ref)
            VALUES (?, ?, ?, ?)
        """, (p["id"], p.get("name", p["id"]), p.get("type", "cloud"), p["id"]))

    # ── 2. Models + provider_models ──
    models_data = load_json(CATALOGUE_DATA_DIR / "models.json", [])
    print(f"  → Modèles bruts : {len(models_data)}")

    model_cache: Dict[str, int] = {}
    pm_count = 0

    for m in models_data:
        provider_ref = m.get("provider_id")
        model_name = m.get("name", "")
        if not model_name or not provider_ref:
            continue

        if "/" in model_name:
            dev, base = model_name.split("/", 1)
            base = base.strip()
        else:
            dev, base = None, model_name.strip()

        if not base:
            continue

        if base not in model_cache:
            meta = {}
            if m.get("is_chat_model") is not None:
                meta["is_chat_model"] = m["is_chat_model"]
            cur.execute("""
                INSERT INTO models (ref, name, developer, metadata_json)
                VALUES (?, ?, ?, ?)
            """, (base, base, dev, json.dumps(meta) if meta else None))
            model_cache[base] = cur.lastrowid

        pid = get_or_create_provider(cur, provider_ref, provider_ref)
        mid = model_cache[base]

        cur.execute("""
            INSERT OR IGNORE INTO provider_models
                (provider_id, model_id, provider_model_name, metadata_json)
            VALUES (?, ?, ?, ?)
        """, (pid, mid, model_name, json.dumps(m) if m else None))
        pm_count += 1

    print(f"    → Modèles uniques : {len(model_cache)}")
    print(f"    → Liens provider↔modèle : {pm_count}")

    # ── 3. API keys (vault + keys.json) ──
    vault_data = load_json(MODELWEAVER_DIR / "vault.json", {})
    keys_data = load_json(MODELWEAVER_DIR / "keys.json", {})
    all_keys: Dict[str, dict] = {}
    for k, v in vault_data.items():
        all_keys[k] = v
    for k, v in keys_data.items():
        all_keys.setdefault(k, v)

    key_count = 0
    for prov_ref, key_info in all_keys.items():
        ak = key_info.get("api_key", "")
        if not ak:
            continue
        cur.execute("SELECT id FROM providers WHERE ref = ?", (prov_ref,))
        row = cur.fetchone()
        if not row:
            continue
        kr = ref()
        cur.execute("""
            INSERT INTO api_keys (ref, identity, provider_id, key_value, tag, health_status, metadata_json)
            VALUES (?, 'default', ?, ?, 'paid', 'unknown', ?)
        """, (kr, row[0], ak,
              json.dumps({"api_base": key_info.get("api_base")}) if key_info.get("api_base") else None))
        key_count += 1

    print(f"  → Clés API : {key_count}")

    # ── 4. Tools (manifest.json + tools.json) ──
    manifest = load_json(BASE_DIR / "manifest.json", {})
    tools_data = load_json(CATALOGUE_DATA_DIR / "tools.json", [])
    tool_count = 0

    for cid, comp in manifest.get("components", {}).items():
        ref_id = comp.get("name", cid)
        ttype = comp.get("type", "binary")
        pkg = comp.get("package")
        inst = comp.get("install_method") or (
            "pip" if ttype == "python-module"
            else "github-release" if ttype == "binary"
            else "direct-url")
        bparams = {"package": pkg} if pkg else {}
        cur.execute("""
            INSERT OR IGNORE INTO tools (ref, name, description, tool_type, install_method,
                current_version, default_download_url, allowed_platforms, allowed_arches,
                installer_params, catalogue_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ref_id, ref_id, comp.get("description"), ttype, inst,
              comp.get("current_version"), comp.get("default_download_url"),
              comp.get("allowed_platforms"), comp.get("allowed_arches"),
              json.dumps(bparams) if bparams else None, ref_id))
        tool_count += 1

    for t in tools_data:
        rid = t.get("id", t.get("name", ""))
        if not rid:
            continue
        cur.execute("""
            INSERT OR IGNORE INTO tools (ref, name, tool_type, install_method, catalogue_ref)
            VALUES (?, ?, 'binary', 'direct-url', ?)
        """, (rid, t.get("name", rid), rid))
        tool_count += 1

    print(f"  → Outils : {tool_count}")

    # ── 5. Commands ──
    commands = [
        ("maj-liste-litellm",    "Mise à jour de la liste LiteLLM depuis models.dev",     "project"),
        ("prepare_keys",         "Préparation des clés API depuis le .env",                "project"),
        ("ordonner-fallback",    "Ordonnancement des modèles de fallback",                 "project"),
        ("modelweaver",          "CLI principale ModelWeaver (bootstrap)",                 "project"),
        ("test_model_connectivity", "Test parallélisé de connectivité des modèles",        "project"),
        ("test_proxy_chat",      "Test de chat via le proxy LiteLLM",                      "project"),
    ]
    for ref_id, desc, ctype in commands:
        cur.execute("""
            INSERT OR IGNORE INTO commands (ref, name, description, command_type)
            VALUES (?, ?, ?, ?)
        """, (ref_id, ref_id, desc, ctype))

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM providers");      pn = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM models");          mn = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM provider_models"); pm = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM api_keys");        kn = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM tools");           tn = cur.fetchone()[0]
    cur.execute("SELECT COUNT(*) FROM commands");        cn = cur.fetchone()[0]
    conn.close()

    print(f"  ✅ Résumé : {pn} providers | {mn} modèles | {pm} liens | {kn} clés | {tn} outils | {cn} commandes")


def migrate_catalogue_db(db_path: Path) -> None:
    print(f"\n  ── BDD catalogue ──")
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")

    execute_schema(conn, SQL_DIR / "catalogue_schema.sql")
    cur = conn.cursor()

    local_path = MODELWEAVER_DIR / "modelweaver.db"
    if not local_path.exists():
        print("  ⚠️  BDD locale introuvable, catalogue vide.")
        conn.commit()
        conn.close()
        return

    local = sqlite3.connect(str(local_path))
    local.row_factory = sqlite3.Row

    for row in local.execute("SELECT ref, name, provider_type, api_type FROM providers"):
        cur.execute("""
            INSERT OR IGNORE INTO catalogue_providers (ref, name, provider_type, api_type)
            VALUES (?, ?, ?, ?)
        """, (row["ref"], row["name"], row["provider_type"], row["api_type"]))

    for row in local.execute(
        "SELECT ref, name, developer, architecture, parameter_count, modality, target_use FROM models"
    ):
        cur.execute("""
            INSERT OR IGNORE INTO catalogue_models (ref, name, developer, architecture, parameter_count, modality, target_use)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (row["ref"], row["name"], row["developer"], row["architecture"],
              row["parameter_count"], row["modality"], row["target_use"]))

    for row in local.execute("SELECT ref, name, description, tool_type, install_method, current_version, default_download_url, allowed_platforms, allowed_arches FROM tools"):
        cur.execute("""
            INSERT OR IGNORE INTO catalogue_tools
                (ref, name, description, tool_type, install_method, current_version,
                 default_download_url, allowed_platforms, allowed_arches)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (row["ref"], row["name"], row["description"], row["tool_type"],
              row["install_method"], row["current_version"], row["default_download_url"],
              row["allowed_platforms"], row["allowed_arches"]))

    for row in local.execute("SELECT ref, name, description, command_type FROM commands"):
        cur.execute("""
            INSERT OR IGNORE INTO catalogue_commands (ref, name, description, command_type)
            VALUES (?, ?, ?, ?)
        """, (row["ref"], row["name"], row["description"], row["command_type"]))

    local.close()
    conn.commit()

    for table in ["catalogue_providers", "catalogue_models", "catalogue_tools", "catalogue_commands"]:
        cur.execute(f"SELECT COUNT(*) FROM {table}")
        print(f"  → {table} : {cur.fetchone()[0]}")

    conn.close()


def main():
    print("=" * 56)
    print("  ModelWeaver — Migration JSON → SQLite")
    print("  Les fichiers JSON ne seront PAS supprimés.")
    print("=" * 56)

    MODELWEAVER_DIR.mkdir(parents=True, exist_ok=True)

    local_db = MODELWEAVER_DIR / "modelweaver.db"
    catalogue_db = MODELWEAVER_DIR / "catalogue.db"

    ts = time.strftime("%Y-%m-%d_%H%M%S")
    backup_dir = MODELWEAVER_DIR / "backups" / f"pre-migration-{ts}"

    for db in [local_db, catalogue_db]:
        if db.exists():
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db, backup_dir / db.name)
            print(f"  📋 Backup → {backup_dir / db.name}")
            db.unlink()

    print()
    migrate_local_db(local_db)
    print()
    migrate_catalogue_db(catalogue_db)

    print(f"\n{'='*56}")
    print("  ✅ Migration terminée. JSON intacts.")
    print(f"{'='*56}")


if __name__ == "__main__":
    main()
