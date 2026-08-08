#!/usr/bin/env python3
"""Agents — DB des agents + WaitForRepository.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Contient : WaitForRepository, AgentsDB.
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
    _default_agents_db, _rows_to_list, _add_column_if_missing,
)
from services._common import mw_home


class WaitForRepository:
    """Agents endormis en attente d'une condition (wait_for).

    Chaque ligne = un agent qui attend une condition (JSON) : type de travail
    dispo (issue_open, task_for_role...). Le waker liste les 'waiting' dont la
    condition est remplie et les réveille — FIFO (les plus anciens d'abord) et
    un à la fois par condition (évite de réveiller tous les agents d'un coup).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def register(self, agent_id: int, condition: dict,
                 expires_at: Optional[str] = None) -> int:
        """Enregistre un agent en attente d'une condition (status waiting).

        UN SEUL wait_for actif par agent : les précédents (waiting/ready) sont
        marqués 'done' avant d'insérer — sinon l'agent s'empile à chaque
        re-endormissement (des centaines de lignes observées).
        """
        self.conn.execute(
            "UPDATE wait_for SET status = 'done' WHERE agent_id = ? "
            "AND status IN ('waiting','ready')", (agent_id,))
        cur = self.conn.execute(
            "INSERT INTO wait_for (agent_id, condition, status, expires_at) "
            "VALUES (?, ?, 'waiting', ?)",
            (agent_id, json.dumps(condition), expires_at))
        self.conn.commit()
        return cur.lastrowid

    def waiting(self) -> List[Dict[str, Any]]:
        """Agents en attente, triés FIFO (les plus anciens d'abord)."""
        cur = self.conn.execute(
            "SELECT * FROM wait_for WHERE status = 'waiting' "
            "ORDER BY created_at ASC, id ASC")
        return _rows_to_list(cur.fetchall())

    def ready(self, agent_id: int, condition: dict) -> bool:
        """Vrai si l'agent attend et que sa condition est remplie (à surcharger
        par le waker via un prédicat). Ici : simple existence d'attente."""
        return True

    def mark_ready(self, wait_id: int) -> None:
        self.conn.execute(
            "UPDATE wait_for SET status = 'ready', ready_at = datetime('now') "
            "WHERE id = ?", (wait_id,))
        self.conn.commit()

    def mark_done(self, agent_id: int) -> None:
        self.conn.execute(
            "UPDATE wait_for SET status = 'done' WHERE agent_id = ? "
            "AND status IN ('waiting','ready')", (agent_id,))
        self.conn.commit()

    def remove_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM wait_for WHERE status != 'done' "
            "AND expires_at IS NOT NULL AND expires_at < datetime('now')")
        self.conn.commit()
        return cur.rowcount


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
        # Autocommit : les agents tournent en threads dans agent-manager et
        # partagent cette connexion. Sans autocommit, une transaction implicite
        # laissée par un thread bloque/imbrique les écritures des autres
        # ("cannot start a transaction within a transaction", "database is
        # locked"). Chaque execute est immédiat.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        self.wait_for = WaitForRepository(self.conn)

    def _ensure_schema(self):
        schema = Path(__file__).resolve().parent / "agents_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())
        # Migration V0.6.8 : storage_json (espace disque proprio par agent)
        _add_column_if_missing(self.conn, "agents", "storage_json", "TEXT")
        # Migration V0.8.5 : nouveaux types de signaux (wakeup, sleep)
        _add_column_if_missing(self.conn, "agent_signals", "source_agent_id", "INTEGER")
        # Migration V0.8.9 : wait_for — agents endormis en attente d'une
        # condition (tâche/issue dispo). Le waker les réveille quand la
        # condition est remplie (un à la fois).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wait_for (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id    INTEGER NOT NULL,
                condition   TEXT NOT NULL,   -- JSON {type, workspace_id, role}
                status      TEXT DEFAULT 'waiting',  -- waiting / ready / done
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                ready_at    TEXT,
                expires_at  TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wait_for_status "
            "ON wait_for(status, agent_id)")
        # Autorisations : demandes d'autorisation persistées (membres→leader→
        # humain). Le request_handler (en mémoire) écrit ici pour que le GUI
        # puisse lister/gérer les demandes. Tous les statuts sont conservés.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id      TEXT NOT NULL UNIQUE,
                agent_id        TEXT NOT NULL,
                team_id         TEXT,
                action          TEXT NOT NULL,   -- path_read | path_write | command
                target          TEXT,            -- JSON {path|command}
                reason          TEXT DEFAULT '',
                scope           TEXT DEFAULT 'once',
                approver_level  TEXT DEFAULT 'leader',  -- leader | human
                request_type    TEXT DEFAULT 'pending_leader',
                status          TEXT DEFAULT 'pending',
                approver_id     TEXT,
                rejection_reason TEXT,
                resolved_scope  TEXT,
                created_at      REAL,
                resolved_at     REAL
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_status "
            "ON auth_requests(status, approver_level)")

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
