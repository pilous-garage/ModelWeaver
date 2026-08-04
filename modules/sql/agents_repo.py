#!/usr/bin/env python3
"""ModelWeaver — Agents Repository.

Extraction depuis db.py : base agents dédiée, WaitForRepository,
AgentDBMixin et helpers associés.

Comportement conservé : autocommit SQLite, locks, WAL, busy_timeout.
"""

import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional


def _ref(prefix: str = "key") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _default_agents_db() -> Path:
    from services._common import mw_home
    return mw_home() / "agents.db"


def _default_local_db() -> Path:
    from services._common import mw_home
    return mw_home() / "modelweaver.db"


def _default_catalogue_db() -> Path:
    from services._common import mw_home
    return mw_home() / "catalogue.db"


def _default_community_db() -> Path:
    from services._common import mw_home
    return mw_home() / "community.db"


def _default_user_db() -> Path:
    from services._common import mw_home
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
#  Helpers migration (copiés de db.py pour éviter dépendance circulaire)
# ──────────────────────────────────────────────

def _add_column_if_missing(conn: sqlite3.Connection, table: str, column: str, decl: str) -> None:
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


# ──────────────────────────────────────────────
#  WaitForRepository
# ──────────────────────────────────────────────

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
        """Enregistre un agent en attente d'une condition (status waiting)."""
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

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def bump_meta(self, key: str) -> None:
        bump_meta(self.conn, key, commit=False)

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  AgentDBMixin — intégration dans ModelWeaverDB
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