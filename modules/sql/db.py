#!/usr/bin/env python3
"""ModelWeaver — Data Access Layer.

Repositories pour les bases modelweaver.db et catalogue.db.
Les modules métier n'écrivent jamais de SQL directement.
"""

import json
import sqlite3
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union
from modules.sql.migrations import MigrationManager
from services._common import mw_home

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


def _ref(prefix: str = "key") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _project_root() -> Path:
    # `sql/` est à la racine du repo. Les chemins DB par défaut (appels
    # ModelWeaverDB()/CatalogueDB() sans argument) pointent sur le répertoire
    # utilisateur ~/.modelweaver — même emplacement que le flux GUI (gui_helper
    # passe des chemins explicites sous ~/.modelweaver) et que les vraies bases.
    return Path.home()


def _default_local_db() -> Path:
    return mw_home() / "modelweaver.db"


def _default_catalogue_db() -> Path:
    return mw_home() / "catalogue.db"


def _default_agents_db() -> Path:
    return mw_home() / "agents.db"


def _default_community_db() -> Path:
    return mw_home() / "community.db"


def _default_user_db() -> Path:
    return mw_home() / "user.db"


# ──────────────────────────────────────────────
#  Utility
# ──────────────────────────────────────────────

def _row_to_dict(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


# ──────────────────────────────────────────────
#  Refresh paresseux de la GUI : signal par DB, pas par table
# ──────────────────────────────────────────────
def read_db_version(conn) -> int:
    """PRAGMA data_version : entier incrémenté à chaque écriture sur le fichier.

    La GUI poll ce compteur par DB à 20 Hz ; s'il change, elle rafraîchit les
    panneaux du domaine correspondant. Pas besoin de triggers par table.
    """
    try:
        return conn.execute("PRAGMA data_version").fetchone()[0]
    except Exception:
        return 0


def read_meta(conn, key: str, default: int = 0) -> int:
    try:
        row = conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return int(row[0]) if row else default
    except Exception:
        return default


def bump_meta(conn, key: str, commit: bool = True) -> None:
    """Incrémente une clé de méta (ex: 'dependencies') pour signaler un changement
    non stocké en table (dépendances système calculées live)."""
    conn.execute(
        "INSERT INTO meta(key, value) VALUES(?,1) "
        "ON CONFLICT(key) DO UPDATE SET value = value + 1",
        (key,),
    )
    if commit:
        conn.commit()


# ──────────────────────────────────────────────
#  Helpers classes_outils (taxonomie métier)
# ──────────────────────────────────────────────

# Mapping par_ref → classe métier (utilisé pour backfill automatique
# quand un outil arrive sans classe_outil_id renseigné, ex: legacy seed).
_DEFAULT_CLASS_MAP = {
    "opencode": "agent",
    "litellm": "router",
    "keyring": "context",
    "ollama": "engine",
    # binaires système détectés
    "python3": "language",
    "git": "dev-tool",
    "curl": "dev-tool",
    "open-webui": "chat-llm",
    "gitingest": "context",
    "requests": "dev-tool",
    "psutil": "system",
    "cryptography": "dev-tool",
}


def _ensure_classes_outils_table(conn) -> None:
    """Crée classes_outils + seed si la table n'existe pas encore.

    Idempotent : appelé par _ensure_schema() du catalogue et du local.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classes_outils'"
    ).fetchone()
    if exists:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classes_outils (
            classe_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ref         TEXT UNIQUE NOT NULL,
            nom         TEXT NOT NULL,
            description TEXT,
            sort_order  INTEGER DEFAULT 0,
            created_at  INTEGER DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO classes_outils (ref, nom, description, sort_order) VALUES (?,?,?,?)",
        [
            ("language", "Languages",       "Interpréteurs et compilateurs",            10),
            ("dev-tool", "Dev Tools",       "Outils de développement",                 20),
            ("ide",      "IDEs",            "Environnements de développement intégrés", 30),
            ("chat-llm", "Chat LLM",        "Interfaces de chat avec les LLM",         40),
            ("agent",    "Agents",          "Orchestrateurs IA autonomes",             50),
            ("engine",   "LLM Engines",     "Moteurs d'exécution locale de LLM",       60),
            ("router",   "Routers",         "Passerelles et proxy LLM",               70),
            ("context",  "Context Tools",   "Gestion du contexte et secrets",          80),
            ("system",   "System Tools",    "Utilitaires système",                     90),
            ("other",    "Other",           "Autres outils",                           999),
        ],
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_outils_ref ON classes_outils(ref)"
    )


def _add_column_if_missing(conn, table: str, column: str, decl: str) -> None:
    """ALTER TABLE idempotent : ajoute `column` à `table` si elle n'existe pas.

    `decl` est la définition SQL de la colonne (ex: 'INTEGER REFERENCES ...').
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def resolve_classe_id(conn, classe_ref: Optional[str]) -> Optional[int]:
    """Traduit une ref de classe (ex: 'agent') en classe_id.

    Retourne None si classe_ref est None/empty ou si la classe n'existe pas.
    Crée la table classes_outils si absente (résilience).
    """
    if not classe_ref:
        return None
    _ensure_classes_outils_table(conn)
    row = conn.execute(
        "SELECT classe_id FROM classes_outils WHERE ref=?", (classe_ref,)
    ).fetchone()
    return row[0] if row else None


def _default_class_for_ref(ref: str) -> str:
    """Retourne la classe métier par défaut pour un ref, 'other' sinon."""
    return _DEFAULT_CLASS_MAP.get(ref, "other")


# ──────────────────────────────────────────────
#  Repositories
# ──────────────────────────────────────────────

class ProviderRepository:
    """Fournisseurs API + liens provider_models."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, provider_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if provider_type:
            cur = self.conn.execute(
                "SELECT * FROM providers WHERE provider_type = ? ORDER BY name",
                (provider_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM providers ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM providers WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def get_by_id(self, pid: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM providers WHERE id = ?", (pid,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM providers WHERE ref = ?", (ref,))
            existing = cur.fetchone()

        if existing:
            self.conn.execute("""
                UPDATE providers SET name=?, provider_type=?, api_type=?,
                    website=?, limits_json=?, rate_limits_json=?,
                    next_reset_at=?, is_free_tier_provider=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("provider_type"),
                data.get("api_type"), data.get("website"),
                data.get("limits_json"), data.get("rate_limits_json"),
                data.get("next_reset_at"), data.get("is_free_tier_provider", 0),
                existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO providers (ref, name, provider_type, api_type, website,
                limits_json, rate_limits_json, next_reset_at, is_free_tier_provider, catalogue_ref)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("prov"), data.get("name"), data.get("provider_type", "cloud"),
            data.get("api_type"), data.get("website"),
            data.get("limits_json"), data.get("rate_limits_json"),
            data.get("next_reset_at"), data.get("is_free_tier_provider", 0),
            data.get("catalogue_ref")
        ))
        return cur.lastrowid

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM providers WHERE ref = ?", (ref,))
        return cur.rowcount > 0

    def get_provider_models(self, provider_ref: str) -> List[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT pm.*, m.ref as model_ref, m.name as model_name, m.developer
            FROM provider_models pm
            JOIN providers p ON p.id = pm.provider_id
            JOIN models m ON m.id = pm.model_id
            WHERE p.ref = ?
            ORDER BY m.name
        """, (provider_ref,))
        return _rows_to_list(cur.fetchall())

    def get_full_details(self, model_ref: Optional[str] = None,
                         provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
        query = """
            SELECT p.ref as provider_ref, p.name as provider_name, p.provider_type,
                   p.api_type, p.limits_json as provider_limits,
                   m.ref as model_ref, m.name as model_name, m.developer,
                   m.architecture, m.parameter_count, m.modality, m.target_use,
                   pm.provider_model_name, pm.context_window_tokens,
                   pm.cost_per_input_token, pm.cost_per_output_token, pm.cost_billing,
                   pm.pricing_rules_json, pm.limits_json, pm.rate_limits_json,
                   pm.status as model_status, pm.next_reset_at
            FROM provider_models pm
            JOIN providers p ON p.id = pm.provider_id
            JOIN models m ON m.id = pm.model_id
            WHERE 1=1
        """
        params = []
        if model_ref:
            query += " AND m.ref = ?"
            params.append(model_ref)
        if provider_ref:
            query += " AND p.ref = ?"
            params.append(provider_ref)
        query += " ORDER BY p.name, m.name"

        cur = self.conn.execute(query, params)
        return _rows_to_list(cur.fetchall())


class ModelRepository:
    """Modèles purs (indépendants des fournisseurs)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, developer: Optional[str] = None,
                 modality: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if developer:
            clauses.append("developer = ?")
            params.append(developer)
        if modality:
            clauses.append("modality = ?")
            params.append(modality)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(f"SELECT * FROM models{where} ORDER BY name", params)
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM models WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def get_by_id(self, mid: int) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM models WHERE id = ?", (mid,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM models WHERE ref = ?", (ref,))
            existing = cur.fetchone()

        if existing:
            self.conn.execute("""
                UPDATE models SET name=?, developer=?, release_year=?, architecture=?,
                    parameter_count=?, modality=?, target_use=?, license=?,
                    is_open_weights=?, parent_model_id=?, metadata_json=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("developer"), data.get("release_year"),
                data.get("architecture"), data.get("parameter_count"),
                data.get("modality"), data.get("target_use"), data.get("license"),
                data.get("is_open_weights", 0), data.get("parent_model_id"),
                data.get("metadata_json"), existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO models (ref, name, developer, release_year, architecture,
                parameter_count, modality, target_use, license, is_open_weights,
                parent_model_id, catalogue_ref, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("model"), data.get("name"), data.get("developer"),
            data.get("release_year"), data.get("architecture"),
            data.get("parameter_count"), data.get("modality"),
            data.get("target_use"), data.get("license"),
            data.get("is_open_weights", 0), data.get("parent_model_id"),
            data.get("catalogue_ref"), data.get("metadata_json")
        ))
        return cur.lastrowid

    def search(self, query: str, modality: Optional[str] = None) -> List[Dict[str, Any]]:
        """Recherche textuelle sur les noms, refs et développeurs."""
        clauses = ["(name LIKE ? OR ref LIKE ? OR developer LIKE ?)"]
        params = [f"%{query}%", f"%{query}%", f"%{query}%"]
        if modality:
            clauses.append("modality = ?")
            params.append(modality)
        where = " WHERE " + " AND ".join(clauses)
        cur = self.conn.execute(f"SELECT * FROM models{where} ORDER BY name LIMIT 50", params)
        return _rows_to_list(cur.fetchall())

    def link_provider(self, provider_id: int, model_id: int,
                      provider_model_name: str, extra: Optional[Dict] = None) -> int:
        """Crée ou met à jour un lien provider→modèle."""
        cur = self.conn.execute(
            "SELECT id FROM provider_models WHERE provider_id = ? AND model_id = ?",
            (provider_id, model_id)
        )
        existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE provider_models SET provider_model_name=?, context_window_tokens=?,
                    max_output_tokens=?, cost_per_input_token=?, cost_per_output_token=?,
                    cost_billing=?, pricing_rules_json=?, limits_json=?,
                    rate_limits_json=?, metadata_json=?, available=?, updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                provider_model_name,
                (extra or {}).get("context_window_tokens"),
                (extra or {}).get("max_output_tokens"),
                (extra or {}).get("cost_per_input_token"),
                (extra or {}).get("cost_per_output_token"),
                (extra or {}).get("cost_billing"),
                (extra or {}).get("pricing_rules_json"),
                (extra or {}).get("limits_json"),
                (extra or {}).get("rate_limits_json"),
                (extra or {}).get("metadata_json"),
                (extra or {}).get("available", 1),
                existing["id"]
            ))
            return existing["id"]

        cur = self.conn.execute("""
            INSERT INTO provider_models (provider_id, model_id, provider_model_name,
                context_window_tokens, max_output_tokens, cost_per_input_token,
                cost_per_output_token, cost_billing, pricing_rules_json,
                limits_json, rate_limits_json, metadata_json, available)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            provider_id, model_id, provider_model_name,
            (extra or {}).get("context_window_tokens"),
            (extra or {}).get("max_output_tokens"),
            (extra or {}).get("cost_per_input_token"),
            (extra or {}).get("cost_per_output_token"),
            (extra or {}).get("cost_billing"),
            (extra or {}).get("pricing_rules_json"),
            (extra or {}).get("limits_json"),
            (extra or {}).get("rate_limits_json"),
            (extra or {}).get("metadata_json"),
            (extra or {}).get("available", 1)
        ))
        return cur.lastrowid


class KeyRepository:
    """Clés API."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, identity: Optional[str] = None,
                 tag: Optional[str] = None,
                 health_status: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        params = []
        if identity:
            clauses.append("identity = ?")
            params.append(identity)
        if tag:
            clauses.append("tag = ?")
            params.append(tag)
        if health_status:
            clauses.append("health_status = ?")
            params.append(health_status)
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        cur = self.conn.execute(f"""
            SELECT ak.*, p.ref as provider_ref, p.name as provider_name
            FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            {where}
            ORDER BY ak.identity, p.name
        """, params)
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT ak.*, p.ref as provider_ref, p.name as provider_name
            FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE ak.ref = ?
        """, (ref,))
        return _row_to_dict(cur.fetchone())

    def get_for_provider(self, provider_ref: str, identity: str = "default") -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("""
            SELECT ak.* FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE p.ref = ? AND ak.identity = ?
            AND ak.locked = 0
            AND ak.health_status IN ('unknown', 'ok', 'degraded')
            ORDER BY ak.health_status = 'ok' DESC, ak.health_status = 'unknown' DESC
            LIMIT 1
        """, (provider_ref, identity))
        return _row_to_dict(cur.fetchone())

    def get_any_for_provider(self, provider_ref: str, identity: str = "default") -> Optional[Dict[str, Any]]:
        """Comme get_for_provider mais ignore le verrou (détecte l'existence)."""
        cur = self.conn.execute("""
            SELECT ak.* FROM api_keys ak
            JOIN providers p ON p.id = ak.provider_id
            WHERE p.ref = ? AND ak.identity = ?
            ORDER BY ak.health_status = 'ok' DESC, ak.health_status = 'unknown' DESC
            LIMIT 1
        """, (provider_ref, identity))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> str:
        ref = data.get("ref") or _ref()
        cur = self.conn.execute("""
            INSERT INTO api_keys (ref, identity, provider_id, key_value, key_display, tag, grade,
                health_status, expiration_date, metadata_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref, data.get("identity", "default"),
            data["provider_id"], data["key_value"],
            data.get("key_display"),
            data.get("tag", "paid"), data.get("grade"),
            data.get("health_status", "unknown"),
            data.get("expiration_date"), data.get("metadata_json")
        ))
        return ref

    def update_health(self, ref: str, status: str, error: Optional[str] = None) -> None:
        self.conn.execute("""
            UPDATE api_keys SET health_status=?, last_error=?,
                last_tested_at=strftime('%s','now'),
                error_count = CASE WHEN ? IS NOT NULL THEN error_count + 1 ELSE error_count END,
                updated_at=strftime('%s','now')
            WHERE ref=?
        """, (status, error, error, ref))

    def update(self, ref: str, tag: Optional[str] = None,
               grade: Optional[str] = None,
               metadata_json: Optional[str] = None) -> None:
        fields, params = [], []
        if tag is not None:
            fields.append("tag = ?"); params.append(tag)
        if grade is not None:
            fields.append("grade = ?"); params.append(grade)
        if metadata_json is not None:
            fields.append("metadata_json = ?"); params.append(metadata_json)
        if not fields:
            return
        fields.append("updated_at = strftime('%s','now')")
        params.append(ref)
        self.conn.execute(
            f"UPDATE api_keys SET {', '.join(fields)} WHERE ref = ?", params)

    def delete(self, ref: str) -> bool:
        cur = self.conn.execute("DELETE FROM api_keys WHERE ref = ?", (ref,))
        return cur.rowcount > 0

    def set_lock(self, ref: str, locked: bool) -> bool:
        cur = self.conn.execute(
            "UPDATE api_keys SET locked = ?, updated_at = strftime('%s','now') WHERE ref = ?",
            (1 if locked else 0, ref))
        return cur.rowcount > 0


class LocalLLMRepository:
    """LLM téléchargés localement."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        if status:
            cur = self.conn.execute(
                "SELECT * FROM local_llms WHERE status = ? ORDER BY name", (status,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM local_llms ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM local_llms WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        ref = data.get("ref")
        existing = None
        if ref:
            cur = self.conn.execute("SELECT id FROM local_llms WHERE ref = ?", (ref,))
            existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE local_llms SET name=?, ram_required_mb=?, chipset=?,
                    launch_command=?, api_base_url=?, context_window_tokens=?,
                    capabilities_json=?, status=?, parameters_json=?,
                    updated_at=strftime('%s','now')
                WHERE id=?
            """, (
                data.get("name"), data.get("ram_required_mb"),
                data.get("chipset"), data.get("launch_command"),
                data.get("api_base_url"), data.get("context_window_tokens"),
                data.get("capabilities_json"), data.get("status"),
                data.get("parameters_json"), existing["id"]
            ))
            return existing["id"]
        cur = self.conn.execute("""
            INSERT INTO local_llms (ref, name, model_id, ram_required_mb, chipset,
                launch_command, api_base_url, context_window_tokens,
                capabilities_json, status, parameters_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ref or _ref("llm"), data.get("name"), data.get("model_id"),
            data.get("ram_required_mb"), data.get("chipset"),
            data.get("launch_command"), data.get("api_base_url"),
            data.get("context_window_tokens"), data.get("capabilities_json"),
            data.get("status", "not_downloaded"), data.get("parameters_json")
        ))
        return cur.lastrowid


class LocalToolRepository:
    """Gestion des outils locaux (local_outils / local_versions / local_installs).

    Calquée sur la même structure que le catalogue (outils/versions/recettes)
    pour permettre plusieurs versions d'un même outil installées par des managers
    différents (ex: litellm pip + conda).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self) -> List[Dict[str, Any]]:
        """Liste tous les outils installés localement."""
        cur = self.conn.execute("""
            SELECT lo.outil_ref, lo.nom, lo.tool_type, c.ref AS classe_ref, c.nom AS classe_nom,
                   lv.nom_version,
                   li.os, li.arch, li.manager, li.package,
                   li.version_installee, li.install_path, li.status
            FROM local_outils lo
            LEFT JOIN classes_outils c ON c.classe_id = lo.classe_outil_id
            JOIN local_versions lv ON lv.local_outil_id = lo.local_outil_id
            JOIN local_installs li ON li.local_version_id = lv.local_version_id
            ORDER BY lo.nom
        """)
        return _rows_to_list(cur.fetchall())

    def get(self, outil_ref: str) -> Optional[Dict[str, Any]]:
        """Dernière installation d'un outil."""
        cur = self.conn.execute("""
            SELECT lo.*, c.ref AS classe_ref, c.nom AS classe_nom, lv.nom_version, li.*
            FROM local_outils lo
            LEFT JOIN classes_outils c ON c.classe_id = lo.classe_outil_id
            JOIN local_versions lv ON lv.local_outil_id = lo.local_outil_id
            JOIN local_installs li ON li.local_version_id = lv.local_version_id
            WHERE lo.outil_ref = ?
            ORDER BY li.ts DESC LIMIT 1
        """, (outil_ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any], classe_ref: Optional[str] = None) -> int:
        """Enregistre une installation locale.

        Crée local_outils et local_versions si inexistants.
        L'upsert de local_installs utilise la clé (version, manager, os, arch).

        classe_ref : ref de la classe métier (ex: 'agent'). Si absent, on
        déduit depuis le ref de l'outil via le mapping par défaut.
        """
        ref = data.get("outil_ref") or data.get("ref")
        if not ref:
            raise ValueError("outil_ref requis")
        _ensure_classes_outils_table(self.conn)
        if classe_ref is None:
            classe_ref = data.get("classe") or _default_class_for_ref(ref)
        classe_id = resolve_classe_id(self.conn, classe_ref)
        # local_outils
        self.conn.execute("""
            INSERT INTO local_outils (outil_ref, nom, tool_type, classe_outil_id)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(outil_ref) DO UPDATE SET
                nom=excluded.nom,
                tool_type=COALESCE(excluded.tool_type, local_outils.tool_type),
                classe_outil_id=COALESCE(excluded.classe_outil_id, local_outils.classe_outil_id)
        """, (ref, data.get("nom", ref), data.get("tool_type"), classe_id))
        lid = self.conn.execute(
            "SELECT local_outil_id FROM local_outils WHERE outil_ref=?", (ref,)
        ).fetchone()[0]
        # local_versions
        ver = data.get("nom_version") or data.get("version") or "latest"
        self.conn.execute("""
            INSERT INTO local_versions (local_outil_id, nom_version)
            VALUES (?, ?)
            ON CONFLICT(local_outil_id, nom_version) DO NOTHING
        """, (lid, ver))
        vid = self.conn.execute(
            "SELECT local_version_id FROM local_versions WHERE local_outil_id=? AND nom_version=?",
            (lid, ver)).fetchone()[0]
        # local_installs
        cur = self.conn.execute(
            "SELECT install_id FROM local_installs WHERE local_version_id=? AND manager=? AND os=? AND arch=?",
            (vid, data.get("manager"), data.get("os", self._os_key()), data.get("arch", self._arch_key())))
        existing = cur.fetchone()
        if existing:
            self.conn.execute("""
                UPDATE local_installs
                SET version_installee=?, install_path=?, package=?, status=?,
                    ts=strftime('%s','now')
                WHERE install_id=?
            """, (data.get("version_installee") or data.get("version"),
                  data.get("install_path"), data.get("package"),
                  data.get("status", "installed"), existing["install_id"]))
            return existing["install_id"]
        cur = self.conn.execute("""
            INSERT INTO local_installs
                (local_version_id, os, arch, manager, package, version_installee, install_path, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (vid,
              data.get("os", self._os_key()), data.get("arch", self._arch_key()),
              data.get("manager"), data.get("package"),
              data.get("version_installee") or data.get("version"),
              data.get("install_path"), data.get("status", "installed")))
        return cur.lastrowid

    def remove(self, ref: str) -> bool:
        """Supprime toutes les installations locales d'un outil."""
        cur = self.conn.execute("""
            DELETE FROM local_installs WHERE local_version_id IN (
                SELECT local_version_id FROM local_versions
                WHERE local_outil_id=(SELECT local_outil_id FROM local_outils WHERE outil_ref=?))
        """, (ref,))
        return cur.rowcount > 0

    @staticmethod
    def _os_key() -> str:
        import platform; return platform.system().lower()

    @staticmethod
    def _arch_key() -> str:
        import platform
        m = platform.machine().lower()
        m = {"amd64": "x86_64", "x86_64": "x86_64", "aarch64": "aarch64", "arm64": "aarch64"}.get(m, m)
        return m


class CommandRepository:
    """Commandes utiles aux IA."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list_all(self, command_type: Optional[str] = None) -> List[Dict[str, Any]]:
        if command_type:
            cur = self.conn.execute(
                "SELECT * FROM commands WHERE command_type = ? ORDER BY name",
                (command_type,)
            )
        else:
            cur = self.conn.execute("SELECT * FROM commands ORDER BY name")
        return _rows_to_list(cur.fetchall())

    def get(self, ref: str) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM commands WHERE ref = ?", (ref,))
        return _row_to_dict(cur.fetchone())

    def save(self, data: Dict[str, Any]) -> int:
        cur = self.conn.execute("""
            INSERT INTO commands (ref, name, description, command_type, catalogue_ref)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data.get("ref") or _ref("cmd"), data.get("name"),
            data.get("description"), data.get("command_type"),
            data.get("catalogue_ref")
        ))
        return cur.lastrowid


# ──────────────────────────────────────────────
#  Tool Classes Repository
# ──────────────────────────────────────────────

class SystemStateRepository:
    """État actuel du système (OS, Archi, Managers)."""
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def save(self, state: Dict[str, Any]) -> None:
        self.conn.execute("""
            INSERT INTO system_state (id, os, architecture, os_version, detected_managers, updated_at)
            VALUES (1, ?, ?, ?, ?, strftime('%s','now'))
            ON CONFLICT(id) DO UPDATE SET
                os=excluded.os, architecture=excluded.architecture,
                os_version=excluded.os_version, detected_managers=excluded.detected_managers,
                updated_at=excluded.updated_at
        """, (
            state.get("os"), state.get("architecture"), state.get("os_version"),
            ",".join(state.get("detected_managers", []))
        ))

    def get(self) -> Optional[Dict[str, Any]]:
        cur = self.conn.execute("SELECT * FROM system_state WHERE id = 1")
        return _row_to_dict(cur.fetchone())



class TursoCatalogueDB:
    """Catalogue distant hébergé sur Turso (libSQL).
    
    Permet des requêtes filtrées sans charger toute la DB en local.
    """
    def __init__(self):
        self.url = os.getenv("TURSO_URL")
        self.token = os.getenv("TURSO_TOKEN")
        if not self.url or not self.token:
            raise RuntimeError("TURSO_URL et TURSO_TOKEN doivent être définis dans .env")
        
        try:
            import libsql
            self.client = libsql.connect(self.url, auth_token=self.token)
        except Exception as e:
            raise RuntimeError(f"Échec de connexion Turso: {e}")

    def get_tool(self, ref: str) -> Optional[Dict[str, Any]]:
        """Récupère un outil depuis le catalogue distant."""
        try:
            cur = self.client.execute("SELECT * FROM catalogue_outils WHERE ref = ?", (ref,))
            row = cur.fetchone()
            if row:
                cols = [d[0] for d in cur.description]
                return dict(zip(cols, row))
            return None
        except Exception as e:
            print(f"❌ Erreur Turso (get_tool): {e}")
            return None

    def list_tools(self, tool_type: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste les outils avec filtre optionnel."""
        query = "SELECT * FROM catalogue_outils"
        params = []
        if tool_type:
            query += " WHERE tool_type = ?"
            params.append(tool_type)
        query += " ORDER BY nom"
        
        try:
            cur = self.client.execute(query, params)
            cols = [d[0] for d in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]
        except Exception as e:
            print(f"❌ Erreur Turso (list_tools): {e}")
            return []

    def _commit(self):
        try:
            self.client.commit()
        except Exception:
            pass

    def upsert_tool(self, tool_data: Dict[str, Any]) -> bool:
        """Ajoute ou met à jour un outil dans le catalogue distant.

        Renseigne classe_outil_id à partir de la classe métier fournie
        (tool_data['classe'] ou tool_data['classe_ref']), sinon déduite
        du ref via le mapping par défaut.
        """
        try:
            _ensure_classes_outils_table(self.client)
            classe_ref = tool_data.get("classe") or tool_data.get("classe_ref")
            if not classe_ref:
                classe_ref = _default_class_for_ref(tool_data.get("ref", ""))
            classe_id = resolve_classe_id(self.client, classe_ref)
        except Exception:
            classe_id = None
        sql = """
        INSERT INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(ref) DO UPDATE SET
            nom=excluded.nom, description=excluded.description,
            tool_type=excluded.tool_type,
            classe_outil_id=COALESCE(excluded.classe_outil_id, catalogue_outils.classe_outil_id)
        """
        params = (
            tool_data.get("ref"), tool_data.get("name") or tool_data.get("nom"),
            tool_data.get("description"), tool_data.get("tool_type"), classe_id,
        )
        try:
            self.client.execute(sql, params)
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_tool): {e}")
            return False

    def get_recipe_content(self, ref: str, mgr: str, os_key: str, arch_key: str) -> Optional[str]:
        """Récupère le contenu YAML d'une recette depuis le catalogue distant."""
        sql = """
        SELECT r.content FROM catalogue_recettes r
        JOIN catalogue_versions v ON v.version_id = r.version_id
        JOIN catalogue_outils o ON o.outil_id = v.outil_id
        WHERE o.ref = ? AND r.manager = ?
          AND r.os IN (?, 'all') AND r.arch IN (?, 'all')
        ORDER BY (r.os = ?) DESC, (r.arch = ?) DESC, r.confidence DESC
        LIMIT 1
        """
        try:
            cur = self.client.execute(sql, (ref, mgr, os_key, arch_key, os_key, arch_key))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            print(f"❌ Erreur Turso (get_recipe_content): {e}")
            return None

    def upsert_version(self, outil_id: int, nom_version: str, description: Optional[str] = None) -> Optional[int]:
        """Ajoute une version dans le catalogue distant et retourne version_id."""
        sql = """
        INSERT INTO catalogue_versions (outil_id, nom_version, description)
        VALUES (?, ?, ?)
        ON CONFLICT(outil_id, nom_version) DO UPDATE SET
            description=COALESCE(excluded.description, catalogue_versions.description)
        """
        try:
            self.client.execute(sql, (outil_id, nom_version, description))
            self._commit()
            cur = self.client.execute(
                "SELECT version_id FROM catalogue_versions WHERE outil_id=? AND nom_version=?",
                (outil_id, nom_version))
            row = cur.fetchone()
            return row[0] if row else None
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_version): {e}")
            return None

    def upsert_recipe(self, version_id: int, os: str, arch: str, manager: str,
                      package: Optional[str] = None, content: Optional[str] = None,
                      confidence: float = 1.0) -> bool:
        """Ajoute ou met à jour une recette dans le catalogue distant."""
        sql = """
        INSERT INTO catalogue_recettes (version_id, os, arch, manager, package, content, confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(version_id, os, arch, manager) DO UPDATE SET
            package=COALESCE(excluded.package, catalogue_recettes.package),
            content=COALESCE(excluded.content, catalogue_recettes.content),
            confidence=excluded.confidence
        """
        try:
            self.client.execute(sql, (version_id, os, arch, manager, package, content, confidence))
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_recipe): {e}")
            return False

    def upsert_popularity(self, outil_id: int, nb_install: int = 0, nb_desinstall: int = 0) -> bool:
        """Ajoute ou met à jour la popularité d'un outil."""
        sql = """
        INSERT INTO outils_popularite (outil_id, nb_install, nb_desinstall)
        VALUES (?, ?, ?)
        ON CONFLICT(outil_id) DO UPDATE SET
            nb_install=excluded.nb_install,
            nb_desinstall=excluded.nb_desinstall,
            updated_at=strftime('%s','now')
        """
        try:
            self.client.execute(sql, (outil_id, nb_install, nb_desinstall))
            self._commit()
            return True
        except Exception as e:
            print(f"❌ Erreur Turso (upsert_popularity): {e}")
            return False


def _cols(cur) -> list:
    """Extrait les noms de colonnes d'un cursor."""
    return [d[0] for d in cur.description] if cur.description else []


def fetch_remote_to_local():
    """Importe le catalogue distant (Turso) vers le local (catalogue.db).

    Bouton manuel : pas d'auto-sync, pas de push local→Turso.
    Retourne un dict {outils, versions, recettes} des compteurs.
    """
    remote = TursoCatalogueDB()
    local = CatalogueDB()

    cur = remote.client.execute("SELECT * FROM catalogue_outils")
    outils = cur.fetchall()
    ocols = _cols(cur)

    out_count = ver_count = rec_count = 0

    for row in outils:
        d = dict(zip(ocols, row))
        local.conn.execute("""
            INSERT INTO catalogue_outils (ref, nom, description, tool_type)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(ref) DO UPDATE SET
                nom=excluded.nom, description=excluded.description, tool_type=excluded.tool_type
        """, (d["ref"], d.get("nom"), d.get("description"), d.get("tool_type")))
        out_count += 1

        lo = local.conn.execute("SELECT outil_id FROM catalogue_outils WHERE ref=?", (d["ref"],)).fetchone()
        if not lo:
            continue
        lid = lo["outil_id"]

        cur2 = remote.client.execute(
            "SELECT * FROM catalogue_versions WHERE outil_id=?", (d["outil_id"],))
        versions = cur2.fetchall()
        vcols = _cols(cur2)

        for vr in versions:
            vd = dict(zip(vcols, vr))
            local.conn.execute("""
                INSERT INTO catalogue_versions (outil_id, nom_version, description)
                VALUES (?, ?, ?)
                ON CONFLICT(outil_id, nom_version) DO UPDATE SET
                    description=COALESCE(excluded.description, catalogue_versions.description)
            """, (lid, vd["nom_version"], vd.get("description")))
            ver_count += 1

            lv = local.conn.execute(
                "SELECT version_id FROM catalogue_versions WHERE outil_id=? AND nom_version=?",
                (lid, vd["nom_version"])).fetchone()
            if not lv:
                continue
            lvid = lv["version_id"]

            cur3 = remote.client.execute(
                "SELECT * FROM catalogue_recettes WHERE version_id=?", (vd["version_id"],))
            recettes = cur3.fetchall()
            rcols = _cols(cur3)

            for rr in recettes:
                rd = dict(zip(rcols, rr))
                local.conn.execute("""
                    INSERT INTO catalogue_recettes (version_id, os, arch, manager, package, content, confidence)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(version_id, os, arch, manager) DO UPDATE SET
                        package=COALESCE(excluded.package, catalogue_recettes.package),
                        content=COALESCE(excluded.content, catalogue_recettes.content),
                        confidence=excluded.confidence
                """, (lvid, rd["os"], rd["arch"], rd["manager"],
                      rd.get("package"), rd.get("content"), rd.get("confidence", 1.0)))
                rec_count += 1

        # Popularité
        cur4 = remote.client.execute(
            "SELECT * FROM outils_popularite WHERE outil_id=?", (d["outil_id"],))
        pop = cur4.fetchone()
        if pop:
            pcols = _cols(cur4)
            pd = dict(zip(pcols, pop))
            local.conn.execute("""
                INSERT INTO outils_popularite (outil_id, nb_install, nb_desinstall)
                VALUES (?, ?, ?)
                ON CONFLICT(outil_id) DO UPDATE SET
                    nb_install=excluded.nb_install, nb_desinstall=excluded.nb_desinstall,
                    updated_at=strftime('%s','now')
            """, (lid, pd.get("nb_install", 0), pd.get("nb_desinstall", 0)))

    local.conn.commit()
    local.close()
    return {"outils": out_count, "versions": ver_count, "recettes": rec_count}


# ──────────────────────────────────────────────
#  Main DB class
# ──────────────────────────────────────────────


class AgentDBMixin:
    """Agent OS repositories (importés séparément pour éviter les dépendances circulaires)."""

    def _init_agent_repos(self):
        from modules.sql.agent_repository import (
            AgentRepository, AgentMessageRepository,
            ModelProviderRepository, SessionRepository, WakeupCallRepository,
        )
        self.model_providers = ModelProviderRepository(self.conn)
        self.agents = AgentRepository(self.conn)
        self.sessions = SessionRepository(self.conn)
        self.agent_messages = AgentMessageRepository(self.conn)
        self.wakeup_calls = WakeupCallRepository(self.conn)


class OrchestrationDBMixin:
    """Orchestration repositories (queue, chatroom, todo, watchers)."""

    def _init_orchestration_repos(self):
        from modules.sql.orchestration_repository import (
            AgentQueueRepository, ChatroomRepository,
            SharedTaskRepository, WatcherRepository, ConnectionRepository,
        )
        self.queue = AgentQueueRepository(self.conn)
        self.chatroom = ChatroomRepository(self.conn)
        self.shared_tasks = SharedTaskRepository(self.conn)
        self.watchers = WatcherRepository(self.conn)
        self.connections = ConnectionRepository(self.conn)


class ModelWeaverDB(AgentDBMixin, OrchestrationDBMixin):
    """Point d'entrée unique pour la base locale.

    Crée automatiquement les tables si elles n'existent pas.

    Usage:
        db = ModelWeaverDB()
        for prov in db.providers.list_all():
            print(prov["name"])
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_local_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

        self.remote_catalogue = None

        # Appliquer les migrations SQL
        migrations_dir = Path(__file__).resolve().parent / "migrations"
        if migrations_dir.exists():
            manager = MigrationManager(self.db_path)
            manager.apply_migrations(migrations_dir)

        self.providers = ProviderRepository(self.conn)
        self.models = ModelRepository(self.conn)
        self.keys = KeyRepository(self.conn)
        self.local_tools = LocalToolRepository(self.conn)
        self.system_state = SystemStateRepository(self.conn)
        self.llms = LocalLLMRepository(self.conn)
        self.commands = CommandRepository(self.conn)
        self._init_agent_repos()
        self._init_orchestration_repos()
        from modules.sql.agent_repository import ScheduledJobRepository
        self.scheduled_jobs = ScheduledJobRepository(self.conn)

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore.

        Applique tout le schema à chaque connexion (sûr grâce à IF NOT EXISTS).
        """
        schema = Path(__file__).resolve().parent / "modelweaver_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())

        # Migration: ajouter key_display à api_keys
        try:
            self.conn.execute("ALTER TABLE api_keys ADD COLUMN key_display TEXT")
        except Exception:
            pass

        # Migration: ajouter locked à api_keys
        try:
            self.conn.execute("ALTER TABLE api_keys ADD COLUMN locked INTEGER DEFAULT 0")
        except Exception:
            pass

        # Table d'état système
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                os TEXT,
                architecture TEXT,
                os_version TEXT,
                detected_managers TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # tool_usage : état d'install local par machine (télémétrie opt-in, phase 2/3).
        # Stocké dans l'inventory (modelweaver.db) car purement local.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                install_id TEXT,
                outil_ref TEXT,
                version_ref TEXT,
                recette_id INTEGER,
                etat TEXT CHECK(etat IN ('installed','uninstalled','upgraded')),
                ts INTEGER DEFAULT (strftime('%s','now'))
            )
        """)

        # ── Migration classes_outils (taxonomie métier) ──
        # Le schéma modelweaver_schema.sql crée déjà classes_outils (+seed)
        # et local_outils(classe_outil_id) pour les DB neuves. Pour les DB
        # locales legacy (local_outils sans classe_outil_id), on ajoute la
        # colonne et on backfill via le mapping par défaut.
        try:
            _ensure_classes_outils_table(self.conn)
            _add_column_if_missing(
                self.conn, "local_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_local_outils_classe "
                    "ON local_outils(classe_outil_id)"
                )
            except Exception:
                pass
            # Backfill : tout outil local sans classe reçoit la classe par défaut
            # déduite de son ref (fallback 'other').
            for row in self.conn.execute(
                "SELECT local_outil_id, outil_ref FROM local_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                cid = resolve_classe_id(self.conn, _default_class_for_ref(row["outil_ref"]))
                if cid is not None:
                    self.conn.execute(
                        "UPDATE local_outils SET classe_outil_id=? WHERE local_outil_id=?",
                        (cid, row["local_outil_id"]))
            self.conn.commit()
        except Exception as e:
            print(f"⚠️  Migration classes_outils (local) ignorée: {e}")

    def scan_installed_tools(self) -> int:
        """Détecte les outils installés et met à jour local_outils/versions/installs."""
        import re, shutil, subprocess, sys, platform
        count = 0
        local_os = platform.system().lower()
        local_arch = platform.machine().lower()
        local_arch = {"amd64": "x86_64", "arm64": "aarch64"}.get(local_arch, local_arch)

        binaries = {
            "ollama": ("ollama", "--version"),
            "opencode": ("opencode", "--version"),
            "python3": ("python3", "--version"),
            "git": ("git", "--version"),
            "curl": ("curl", "--version"),
        }
        for ref, (cmd, ver_flag) in binaries.items():
            path = shutil.which(cmd)
            if not path:
                continue
            version = None
            try:
                r = subprocess.run([cmd, ver_flag], capture_output=True, text=True, timeout=5)
                m = re.search(r"(\d+\.\d+(?:\.\d+)?)", r.stdout or "")
                version = m.group(1) if m else None
            except Exception:
                pass
            self.local_tools.save({
                "outil_ref": ref, "nom": ref, "tool_type": "binary",
                "nom_version": version or "unknown", "manager": "binary",
                "os": local_os, "arch": local_arch,
                "version_installee": version or "unknown",
                "install_path": path, "status": "installed",
            })
            count += 1

        pip_tools = {
            "litellm": "litellm", "open-webui": "open_webui", "gitingest": "gitingest",
            "keyring": "keyring", "requests": "requests", "psutil": "psutil",
            "cryptography": "cryptography",
        }
        try:
            r = subprocess.run([sys.executable, "-m", "pip", "list", "--format=json"],
                               capture_output=True, text=True, timeout=10)
            if r.returncode == 0:
                import json
                pip_packages = json.loads(r.stdout)
                pip_map = {p["name"].lower().replace("-", "_"): p["version"]
                           for p in pip_packages}
                for ref, pkg_name in pip_tools.items():
                    version = pip_map.get(pkg_name.lower())
                    if not version:
                        continue
                    self.local_tools.save({
                        "outil_ref": ref, "nom": ref, "tool_type": "python-module",
                        "nom_version": version, "manager": "pip",
                        "os": local_os, "arch": local_arch,
                        "version_installee": version,
                        "install_path": sys.executable, "status": "installed",
                    })
                    count += 1
        except Exception:
            pass
        self.commit()
        return count

    @contextmanager
    def transaction(self):
        """Gestionnaire de contexte pour les transactions."""
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


class CatalogueDB:
    """Point d'entrée pour la base catalogue (distant / synchro).

    Crée automatiquement les tables si elles n'existent pas.
    Usage:
        cat = CatalogueDB()
        cat.sync_from_url("http://localhost:8765/api")
        cat.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_catalogue_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

    def _ensure_schema(self):
        """Crée les tables si elles n'existent pas encore.
        Le script SQL inclut un seed idempotent (INSERT OR IGNORE) des
        providers. Si la BDD préexiste mais est vide (cas d'un catalogue
        initialisé sans seed), on rejoue le script pour peupler les providers."""
        schema = Path(__file__).resolve().parent / "catalogue_schema.sql"
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalogue_providers'"
        )
        if not cur.fetchone():
            if schema.exists():
                self.conn.executescript(schema.read_text())
        else:
            # Migration: ajoute provider_models si manquant
            try:
                self.conn.execute("""
                    CREATE TABLE IF NOT EXISTS provider_models (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                        model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                        provider_model_name TEXT NOT NULL,
                        context_window_tokens INTEGER,
                        max_output_tokens INTEGER,
                        cost_per_input_token TEXT,
                        cost_per_output_token TEXT,
                        status TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','experimental')),
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(provider_id, model_id))
                """)
            except Exception:
                pass
            # Migration: nouvelles tables catalogue outils/versions/recettes/popularité
            try:
                self.conn.executescript("""
                    CREATE TABLE IF NOT EXISTS catalogue_outils (
                        outil_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ref TEXT UNIQUE NOT NULL, nom TEXT NOT NULL,
                        fabricant TEXT, description TEXT,
                        tool_type TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
                        created_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS catalogue_versions (
                        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        outil_id INTEGER NOT NULL REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nom_version TEXT NOT NULL, description TEXT,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(outil_id, nom_version));
                    CREATE TABLE IF NOT EXISTS catalogue_recettes (
                        recette_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version_id INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
                        os TEXT NOT NULL DEFAULT 'all', arch TEXT NOT NULL DEFAULT 'all',
                        manager TEXT, package TEXT, confidence REAL DEFAULT 1.0,
                        createur_id TEXT DEFAULT 'system',
                        install_count INTEGER DEFAULT 0, uninstall_count INTEGER DEFAULT 0,
                        content TEXT, enabled INTEGER DEFAULT 1,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS outils_popularite (
                        outil_id INTEGER PRIMARY KEY REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nb_install INTEGER DEFAULT 0, nb_desinstall INTEGER DEFAULT 0,
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
                    CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
                """)
            except Exception:
                pass
            # Migration: ajouter tool_type aux DB existantes
            try:
                self.conn.execute("ALTER TABLE catalogue_outils ADD COLUMN tool_type TEXT")
            except Exception:
                pass

        # ── Migration classes_outils (taxonomie métier) ──
        # Couvre les deux branches ci-dessus :
        #  - DB vierge (schéma chargé via catalogue_schema.sql) : la table
        #    classes_outils + la colonne classe_outil_id sont déjà créées ;
        #  - DB legacy (branche else) : il faut les créer/ajouter.
        #  - DB legacy partielle où catalogue_outils existait DÉJÀ au chargement
        #    du .sql (IF NOT EXISTS ignoré) : on ajoute la colonne ici.
        try:
            _ensure_classes_outils_table(self.conn)
            _add_column_if_missing(
                self.conn, "catalogue_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outils_classe "
                    "ON catalogue_outils(classe_outil_id)"
                )
            except Exception:
                pass
            # Backfill : tout outil sans classe_outil_id reçoit la classe par défaut
            # déduite de son ref (fallback 'other').
            for row in self.conn.execute(
                "SELECT outil_id, ref FROM catalogue_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                classe_ref = _default_class_for_ref(row["ref"])
                cid = resolve_classe_id(self.conn, classe_ref)
                if cid is not None:
                    self.conn.execute(
                        "UPDATE catalogue_outils SET classe_outil_id=? WHERE outil_id=?",
                        (cid, row["outil_id"]))
            self.conn.commit()
        except Exception as e:
            print(f"⚠️  Migration classes_outils ignorée: {e}")

        # ── Seed si tables vides ──
        # Couvre le cas d'une BDD pré-existante (tables créées) mais non
        # peuplée : le seed INSERT OR IGNORE du .sql est idempotent.
        try:
            pc = self.conn.execute("SELECT COUNT(*) FROM catalogue_providers").fetchone()[0]
            ec = 0
            try:
                ec = self.conn.execute("SELECT COUNT(*) FROM provider_endpoints").fetchone()[0]
            except Exception:
                ec = 0
            if (pc == 0 or ec == 0) and schema.exists():
                self.conn.executescript(schema.read_text())
                self.conn.commit()
        except Exception as e:
            print(f"⚠️  Seed ignoré: {e}")

        # ── Migration colonnes provider_endpoints ──
        try:
            _add_column_if_missing(self.conn, "provider_endpoints", "local_latency", "REAL")
            _add_column_if_missing(self.conn, "provider_endpoints", "global_quality", "REAL")
        except Exception as e:
            print(f"⚠️  Migration provider_endpoints ignorée: {e}")

        # ── Migration context_window_effective + context_audit_log ──
        try:
            _add_column_if_missing(self.conn, "provider_models", "context_window_effective", "INTEGER")
            _add_column_if_missing(self.conn, "provider_models", "available", "INTEGER DEFAULT 1")
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS context_audit_log (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref          TEXT NOT NULL,
                    model_ref             TEXT NOT NULL,
                    tokens_sent           INTEGER NOT NULL,
                    detected_context_limit INTEGER,
                    context_window_effective INTEGER,
                    created_at            INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_provider_model "
                "ON context_audit_log(provider_ref, model_ref)")
        except Exception as e:
            print(f"⚠️  Migration context_audit_log ignorée: {e}")

        # ── Migration : nouvelles tables d'acces modeles (V0.7.0.4+) ──
        try:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS key_endpoint_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    key_ref TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    provider_model_name TEXT NOT NULL,
                    declared INTEGER DEFAULT 0,
                    available INTEGER DEFAULT 0,
                    last_checked_at INTEGER,
                    last_error TEXT,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(endpoint_id, key_ref, model_id)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS model_efficacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    use_case TEXT NOT NULL,
                    score_quality REAL DEFAULT 0, score_speed REAL DEFAULT 0,
                    score_cost REAL DEFAULT 0, score_reliability REAL DEFAULT 0,
                    samples INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, use_case)
                )
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                    unit TEXT, scope TEXT
                )
            """)
            self.conn.execute("""
                INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
                    ('req_per_min','Requetes / minute','requests','requests'),
                    ('req_per_day','Requetes / jour','requests','requests'),
                    ('tok_per_min','Tokens / minute','tokens','tokens'),
                    ('tok_per_hour','Tokens / heure','tokens','tokens'),
                    ('tok_per_day','Tokens / jour','tokens','tokens'),
                    ('cost_per_day','Cout / jour (USD)','usd','cost'),
                    ('cost_per_month','Cout / mois (USD)','usd','cost')
            """)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL CHECK(target_type IN ('provider','model','endpoint','key')),
                    target_ref TEXT NOT NULL,
                    tag_id INTEGER NOT NULL REFERENCES budget_tags(id),
                    limit_value REAL NOT NULL,
                    window TEXT NOT NULL DEFAULT 'day' CHECK(window IN ('minute','hour','day','month')),
                    cost_per_unit REAL,
                    created_at INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
        except Exception as e:
            print(f"⚠️  Migration tables d'acces ignoree: {e}")

        # ── Seed modèles + provider_models si vides ──
        # S'exécute pour TOUTE BDD (vierge OU pré-existante) : le script
        # SQL crée les tables mais ne seede PAS les modèles (ceux-ci
        # viennent de modules/llm_manager/data/*.json). Indépendant des
        # outils — couvre le cas d'un démarrage GUI frais où le
        # catalogue LLM n'a jamais été seedé, OU où seed_catalogue a
        # seedé les modèles mais laissé provider_models vide (transaction
        # ouverte par sync_tools en amont → snapshot vide au seed_provider_models).
        # NB : on commit APRES seed_models pour que provider_models
        # (qui requête catalogue_models) ne voie pas un snapshot vide.
        try:
            mc = self.conn.execute("SELECT COUNT(*) FROM catalogue_models").fetchone()[0]
            pmc = 0
            try:
                pmc = self.conn.execute("SELECT COUNT(*) FROM provider_models").fetchone()[0]
            except Exception:
                pmc = 0
            if mc == 0 or pmc == 0:
                from modules.llm_manager.llm_manager import seed_models, seed_provider_models
                if mc == 0:
                    seed_models(self)
                    self.conn.commit()
                if pmc == 0:
                    seed_provider_models(self)
                    self.conn.commit()
        except Exception as e:
            print(f"⚠️  Seed modèles ignoré: {e}")

    @contextmanager
    def transaction(self):
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def sync_providers(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_providers
                    (ref, name, provider_type, api_type, website, is_free_tier_provider)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("provider_type"), r.get("api_type"),
                  r.get("website"), r.get("is_free_tier_provider", 0)))
            count += 1
        return count

    def sync_models(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            # Rétro-compat : models.json utilise 'id' au lieu de 'ref'
            ref = r.get("ref") or r.get("id")
            if not ref:
                continue
            name = r.get("name") or ref
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_models
                    (ref, name, developer, release_year, architecture, parameter_count,
                     modality, target_use, license, is_open_weights, parent_model_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ref, name, r.get("developer"), r.get("release_year"),
                  r.get("architecture"), r.get("parameter_count"), r.get("modality"),
                  r.get("target_use"), r.get("license"), r.get("is_open_weights", 0),
                  r.get("parent_model_ref")))
            count += 1
        return count

    def sync_tools(self, rows: List[Dict]) -> int:
        count = 0
        _ensure_classes_outils_table(self.conn)
        for r in rows:
            # classe_outil_id : depuis la classe fournie, sinon déduite du ref
            classe_ref = r.get("classe") or r.get("classe_ref") or _default_class_for_ref(r.get("ref", ""))
            classe_id = resolve_classe_id(self.conn, classe_ref)
            # catalogue_outils
            self.conn.execute("""
                INSERT INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    nom=excluded.nom, description=excluded.description,
                    tool_type=COALESCE(excluded.tool_type, catalogue_outils.tool_type),
                    classe_outil_id=COALESCE(excluded.classe_outil_id, catalogue_outils.classe_outil_id)
            """, (r["ref"], r["name"], r.get("description"), r.get("tool_type"), classe_id))
            # catalogue_versions (current_version)
            ver = r.get("current_version")
            if ver:
                oid = self.conn.execute(
                    "SELECT outil_id FROM catalogue_outils WHERE ref=?", (r["ref"],)
                ).fetchone()[0]
                self.conn.execute("""
                    INSERT INTO catalogue_versions (outil_id, nom_version, description)
                    VALUES (?, ?, ?)
                    ON CONFLICT(outil_id, nom_version) DO NOTHING
                """, (oid, ver, r.get("description")))
            count += 1
        return count

    def sync_commands(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_commands
                    (ref, name, description, command_type)
                VALUES (?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("description"), r.get("command_type")))
            count += 1
        return count

    def sync_from_url(self, url: str) -> Dict[str, int]:
        """Synchronise toutes les tables catalogue depuis une URL distante.

        L'URL doit exposer les endpoints :
          {url}/providers, {url}/models, {url}/tools, {url}/commands

        Retourne {table: count} des entrées synchronisées.
        """
        import urllib.request
        import json

        endpoints = {
            "providers": self.sync_providers,
            "models": self.sync_models,
            "tools": self.sync_tools,
            "commands": self.sync_commands,
        }
        results = {}

        for name, sync_fn in endpoints.items():
            endpoint = f"{url.rstrip('/')}/{name}"
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "ModelWeaver/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    if isinstance(data, list):
                        results[name] = sync_fn(data)
                    else:
                        print(f"  ⚠️  {name}: format inattendu")
                        results[name] = 0
                print(f"  ✅ {name}: {results[name]} entrées synchronisées")
            except Exception as e:
                print(f"  ⚠️  {name}: échec synchro ({e})")
                results[name] = -1

        self.conn.commit()
        return results

    def close(self):
        self.conn.close()

    # ── Popularité / télémétrie (compteurs agrégés) ──
    def bump_popularity(self, ref: str, action: str) -> None:
        """Incrémente les compteurs d'install/désinstall pour un outil.

        action: 'install' -> nb_install +1 ; 'uninstall' -> nb_desinstall +1.
        Gestion triviale (+1/-1) ; la réconciliation distante vient plus tard.
        """
        row = self.conn.execute(
            "SELECT outil_id FROM catalogue_outils WHERE ref=?", (ref,)).fetchone()
        if not row:
            return
        oid = row["outil_id"]
        self.conn.execute("INSERT OR IGNORE INTO outils_popularite (outil_id) VALUES (?)", (oid,))
        if action == "install":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_install=nb_install+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET install_count=install_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        elif action == "uninstall":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_desinstall=nb_desinstall+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET uninstall_count=uninstall_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        self.conn.commit()

    def get_catalogue_tools(self, os_key: str = "linux", arch_key: str = "x86_64") -> dict:
        """Liste les outils du catalogue compatibles avec l'OS/arch local.

        Pour chaque outil on retourne la liste des managers disponibles
        (recettes compatibles), ce qui remplace l'ancien install_method
        (désormais dans catalogue_recettes.manager).

        Chaque outil porte aussi sa classe métier (classe_ref + classe_nom),
        résolue via LEFT JOIN sur classes_outils (fallback 'other').
        """
        cur = self.conn.execute("""
            SELECT DISTINCT o.ref, o.nom, o.description, o.tool_type,
                   c.ref AS classe_ref, c.nom AS classe_nom,
                   r.manager, r.package, r.os, r.arch, r.confidence
            FROM catalogue_outils o
            LEFT JOIN classes_outils c ON c.classe_id = o.classe_outil_id
            JOIN catalogue_versions v ON v.outil_id = o.outil_id
            JOIN catalogue_recettes r ON r.version_id = v.version_id
            WHERE r.os IN (?, 'all') AND r.arch IN (?, 'all') AND r.enabled = 1
            ORDER BY o.nom, r.manager
        """, (os_key, arch_key))
        cols = [d[0] for d in cur.description]
        tools_map = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            ref = d["ref"]
            if ref not in tools_map:
                classe_ref = d["classe_ref"] or _default_class_for_ref(ref)
                classe_nom = d["classe_nom"] or classe_ref
                tools_map[ref] = {
                    "ref": ref, "name": d["nom"],
                    "description": d["description"],
                    "tool_type": d["tool_type"],
                    "classe_ref": classe_ref,
                    "classe": classe_nom,
                    "managers": [],
                }
            tools_map[ref]["managers"].append({
                "manager": d["manager"], "package": d["package"],
                "os": d["os"], "arch": d["arch"], "confidence": d["confidence"],
            })
        tools = list(tools_map.values())
        return {"tools": tools, "count": len(tools)}


# ──────────────────────────────────────────────
#  RuntimeDB : écritures haute fréquence
# ──────────────────────────────────────────────
def _default_runtime_db() -> Path:
    return mw_home() / "runtime.db"


class RuntimeDB:
    """DB isolée pour les données runtime : processus, services, jobs d'install.

    Le GUI (Rust) y écrit directement en haute fréquence (mirror processus/
    services) ; le daemon et l'installer_worker écrivent aussi install_jobs.
    Isolée de l'inventaire/catalogue pour éviter la contention SQLite.
    Séparation physique : la GUI ne poll PAS table par table, elle compare
    `PRAGMA data_version` de cette DB (voir `read_db_version`).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or _default_runtime_db())
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS processes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                pid INTEGER,
                parent_id INTEGER,
                status TEXT,
                command TEXT,
                log_path TEXT,
                cpu REAL,
                rss_kb INTEGER,
                started_at INTEGER,
                ended_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS services (
                name TEXT PRIMARY KEY,
                mode TEXT,
                command TEXT,
                args TEXT,
                status TEXT,
                pid INTEGER,
                parent TEXT,
                restart INTEGER,
                restarts INTEGER DEFAULT 0,
                last_exit INTEGER,
                started_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS install_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL,
                name TEXT,
                job_type TEXT,
                status TEXT,
                log TEXT,
                pid INTEGER,
                created_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        # meta : clés de signal hors-table (ex: 'dependencies' pour le refresh GUI)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── Migration : tables usage & mesure locales (V0.7.0.4+) ──
        # modelweaver.db privé, jamais poussé au distant.
        try:
            self.conn.executescript("""
                CREATE TABLE IF NOT EXISTS real_call_models (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref  TEXT,
                    endpoint_id   INTEGER,
                    key_ref       TEXT,
                    model_ref     TEXT,
                    agent_id      TEXT,
                    sent_at       INTEGER NOT NULL,
                    received_at   INTEGER,
                    tokens_in     INTEGER DEFAULT 0,
                    tokens_out    INTEGER DEFAULT 0,
                    cost          REAL DEFAULT 0,
                    status        TEXT CHECK(status IN ('ok','rate_limited','error','quota_exhausted')),
                    error_code    TEXT,
                    error_detail  TEXT,
                    window_key    TEXT,
                    created_at    INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_rcm_model ON real_call_models(model_ref);
                CREATE INDEX IF NOT EXISTS idx_rcm_sent ON real_call_models(sent_at);
                CREATE INDEX IF NOT EXISTS idx_rcm_status ON real_call_models(status);

                CREATE TABLE IF NOT EXISTS really_used_budget (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_tag_code TEXT NOT NULL,
                    target_type   TEXT NOT NULL,
                    target_ref    TEXT NOT NULL,
                    window        TEXT,
                    measured_limit REAL,
                    sample_count  INTEGER DEFAULT 0,
                    first_exhausted_at INTEGER,
                    confidence    REAL DEFAULT 0,
                    method        TEXT,
                    measured_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(budget_tag_code, target_type, target_ref, window)
                );

                CREATE TABLE IF NOT EXISTS endpoint_model_usage (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint_id   INTEGER,
                    model_ref     TEXT,
                    agent_id      TEXT,
                    requests      INTEGER DEFAULT 0,
                    tokens_in     INTEGER DEFAULT 0,
                    tokens_out    INTEGER DEFAULT 0,
                    cost          REAL DEFAULT 0,
                    last_call_at  INTEGER,
                    last_call_working INTEGER DEFAULT 1,
                    error_count   INTEGER DEFAULT 0,
                    created_at    INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_emu_endpoint ON endpoint_model_usage(endpoint_id);
                CREATE INDEX IF NOT EXISTS idx_emu_model ON endpoint_model_usage(model_ref);

                CREATE TABLE IF NOT EXISTS budget_consumption (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_id     INTEGER NOT NULL,
                    used          REAL DEFAULT 0,
                    updated_at    INTEGER DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS local_model_efficacy (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_ref     TEXT NOT NULL,
                    use_case      TEXT NOT NULL,
                    score_quality   REAL DEFAULT 0,
                    score_speed     REAL DEFAULT 0,
                    score_cost      REAL DEFAULT 0,
                    score_reliability REAL DEFAULT 0,
                    samples       INTEGER DEFAULT 0,
                    criteria_meta TEXT,
                    last_evaluated_at INTEGER,
                    UNIQUE(model_ref, use_case)
                );

                CREATE TABLE IF NOT EXISTS agent_actif (
                    agent_id        TEXT PRIMARY KEY,
                    status          TEXT,
                    current_step    TEXT,
                    last_heartbeat  INTEGER,
                    calls_count     INTEGER DEFAULT 0,
                    tokens_total    INTEGER DEFAULT 0,
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_agent_actif_hb ON agent_actif(last_heartbeat);
            """)
        except Exception as e:
            print(f"⚠️  Migration tables usage ignorée: {e}")

        self.conn.commit()

    def data_version(self) -> int:
        return read_db_version(self.conn)

    def bump_meta(self, key: str, commit: bool = True):
        bump_meta(self.conn, key, commit=commit)

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  AgentsDB — Base dédiée aux agents
# ──────────────────────────────────────────────

class AgentsDB:
    """Point d'entrée pour la base agents (domaine distinct).

    Banque séparée de modelweaver.db — contient l'identité, le runtime,
    les métriques et les signaux des agents.

    Usage:
        db = AgentsDB()
        db.conn.execute("SELECT * FROM agents")
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_agents_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

    def _ensure_schema(self):
        schema = Path(__file__).resolve().parent / "agents_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())
        # Migration V0.6.8 : storage_json (espace disque proprio par agent)
        _add_column_if_missing(self.conn, "agents", "storage_json", "TEXT")

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def bump_meta(self, key: str) -> None:
        bump_meta(self.conn, key, commit=False)

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  Quick test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    db = ModelWeaverDB()
    print(f"🔌 Connecté à {db.db_path}")

    print(f"\n  Providers : {len(db.providers.list_all())}")
    print(f"  Modèles   : {len(db.models.list_all())}")
    print(f"  Clés      : {len(db.keys.list_all())}")
    print(f"  LLMs locaux: {len(db.llms.list_all())}")
    print(f"  Commandes : {len(db.commands.list_all())}")

    g = db.providers.get("groq")
    print(f"\n  groq → {g}")

    print("\n  Recherche 'gemini':")
    for m in db.models.search("gemini", modality="text"):
        print(f"    → {m['ref']} ({m['developer']})")

    db.close()

    cat = CatalogueDB()
    print(f"\n🔌 Catalogue: {cat.db_path}")
    cat.close()
