"""MigrationManager — Gestion des versions du schéma SQLite.

Permet d'appliquer des scripts SQL de migration de manière séquentielle.
Contient également les helpers de migration inline utilisés par db.py
(colonnes manquantes, classes_outils, etc.).
"""

import sqlite3
import os
import logging
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger("modelweaver.migrations")


# ──────────────────────────────────────────────
#  Helpers migrations inline (extraits de db.py)
# ──────────────────────────────────────────────

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
#  MigrationManager
# ──────────────────────────────────────────────

class MigrationManager:
    """Gère l'application des scripts de migration SQL."""

    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._ensure_migration_table()

    def _ensure_migration_table(self):
        """Crée la table de suivi des migrations si elle n'existe pas."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()

    def get_applied_versions(self) -> List[int]:
        """Liste les versions déjà appliquées."""
        with sqlite3.connect(str(self.db_path)) as conn:
            cur = conn.execute("SELECT version FROM schema_migrations ORDER BY version ASC")
            return [row[0] for row in cur.fetchall()]

    def apply_migrations(self, migrations_dir: Path):
        """Applique tous les scripts de migration manquants."""
        applied = self.get_applied_versions()

        # On liste les fichiers .sql dans le dossier migrations
        # Format attendu : 001_description.sql, 002_...
        scripts = sorted([f for f in migrations_dir.glob("*.sql") if f.suffix == ".sql"])

        applied_count = 0
        for script in scripts:
            try:
                version = int(script.name.split("_")[0])
            except (ValueError, IndexError):
                logger.warning("Fichier de migration ignoré (format incorrect) : %s", script.name)
                continue

            if version not in applied:
                logger.info("Application de la migration %s...", script.name)
                self._execute_script(script)
                self._record_migration(version)
                applied_count += 1

        return applied_count

    def _execute_script(self, script_path: Path):
        """Exécute le contenu d'un script SQL."""
        with sqlite3.connect(str(self.db_path)) as conn:
            try:
                conn.executescript(script_path.read_text())
                conn.commit()
            except sqlite3.Error as e:
                logger.error("Erreur lors de la migration %s : %s", script_path.name, e)
                raise e

    def _record_migration(self, version: int):
        """Enregistre la migration comme appliquée."""
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("INSERT INTO schema_migrations (version) VALUES (?)", (version,))
            conn.commit()
