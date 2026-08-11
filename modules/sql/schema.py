#!/usr/bin/env python3
"""Schéma SQL — helpers de bas niveau pour les bases ModelWeaver.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Ces helpers ne dépendent d'aucune classe : ils sont la base des modules
catalogue_repo, modelweaver_repo, agents_repo, runtime_repo.
"""

import json
import sqlite3
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from services._common import mw_home


def _ref(prefix: str = "key") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _project_root() -> Path:
    # `sql/` est à la racine du repo. Les chemins DB par défaut (appels
    # ModelWeaverDB()/CatalogueDB() sans argument) pointent sur le répertoire
    # utilisateur ~/.modelweaver — même emplacement que les bases de données
    # du daemon API et que les vraies bases.
    return Path.home()


def _default_local_db() -> Path:
    return mw_home() / "modelweaver.db"


def _default_catalogue_db() -> Path:
    return mw_home() / "catalogue.db"


def _default_local_catalogue_db() -> Path:
    return mw_home() / "local_catalogue.db"


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


def _default_runtime_db() -> Path:
    return mw_home() / "runtime.db"
