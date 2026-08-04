#!/usr/bin/env python3
"""ModelWeaver — DB locale + mixins d'orchestration/agents.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Contient : AgentDBMixin, OrchestrationDBMixin, ModelWeaverDB.
"""

import json
import sqlite3
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from modules.sql.schema import (
    _ref, _default_local_db, _row_to_dict, _rows_to_list,
    _add_column_if_missing, _ensure_classes_outils_table,
)
from modules.sql.catalogue_repo import (
    ProviderRepository, KeyRepository, CommandRepository,
    ModelRepository, LocalLLMRepository, LocalToolRepository,
    SystemStateRepository,
)
from modules.sql.migrations import MigrationManager
from services._common import mw_home


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
        # Autocommit (voir CatalogueDB) : évite les locks d'écriture persistants
        # entre les services partageant modelweaver.db.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
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
            self.conn.rollback()        # Migration: ajouter locked à api_keys
        try:
            self.conn.execute("ALTER TABLE api_keys ADD COLUMN locked INTEGER DEFAULT 0")
        except Exception:
            self.conn.rollback()

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
            self.conn.rollback()
            print(f"⚠️  Migration classes_outils (local) ignorée: {e}")
        # Commit final : ferme la transaction implicite du DDL ci-dessus
        # (sinon le lock d'écriture WAL reste tenu par le process toute sa vie).
        try:
            self.conn.commit()
        except Exception:
            self.conn.rollback()

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


