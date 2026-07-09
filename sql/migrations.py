"""MigrationManager — Gestion des versions du schéma SQLite.

Permet d'appliquer des scripts SQL de migration de manière séquentielle.
"""

import sqlite3
import os
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger("modelweaver.migrations")


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
