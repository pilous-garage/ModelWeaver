"""PauseManager — Gestion multi-niveau de la pause (projet/team/agent)."""

import sqlite3
import threading
import time
from enum import Enum
from pathlib import Path
from typing import Dict, Optional


class PauseLevel(str, Enum):
    PROJECT = "project"
    TEAM = "team"
    AGENT = "agent"


class PauseStore:
    """Stockage SQLite des états de pause."""

    def __init__(self, db_path=None):
        path = Path(db_path) if db_path else (Path(__file__).parent.parent / "pause_store.db")
        path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._init_db()

    def _init_db(self):
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pause_state (
                level TEXT NOT NULL,
                ref_id TEXT NOT NULL,
                paused INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL,
                PRIMARY KEY (level, ref_id)
            )
        """)
        self._conn.commit()

    def set_paused(self, level, ref_id, paused):
        with self._lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO pause_state (level, ref_id, paused, updated_at) VALUES (?, ?, ?, ?)",
                (level.value, str(ref_id), 1 if paused else 0, time.time()),
            )
            self._conn.commit()

    def is_paused(self, level, ref_id):
        with self._lock:
            row = self._conn.execute(
                "SELECT paused FROM pause_state WHERE level = ? AND ref_id = ?",
                (level.value, str(ref_id)),
            ).fetchone()
            return bool(row["paused"]) if row else False

    def get_agent_pause_status(self, agent_id, project_id=None, team_id=None):
        status = {
            "project_paused": False,
            "team_paused": False,
            "agent_paused": False,
            "effective_paused": False,
        }
        if project_id:
            status["project_paused"] = self.is_paused(PauseLevel.PROJECT, project_id)
        if team_id:
            status["team_paused"] = self.is_paused(PauseLevel.TEAM, team_id)
        status["agent_paused"] = self.is_paused(PauseLevel.AGENT, str(agent_id))
        status["effective_paused"] = status["project_paused"] or status["team_paused"] or status["agent_paused"]
        return status


class PauseManager:
    """Singleton pour la gestion des pauses multi-niveau."""

    _instance = None
    _lock = threading.Lock()

    def __init__(self, db_path=None):
        self.store = PauseStore(db_path)

    @classmethod
    def get_instance(cls, db_path=None):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    def pause(self, level, ref_id):
        self.store.set_paused(level, ref_id, True)

    def resume(self, level, ref_id):
        self.store.set_paused(level, ref_id, False)

    def is_paused(self, level, ref_id):
        return self.store.is_paused(level, ref_id)

    def get_status(self, agent_id, project_id=None, team_id=None):
        return self.store.get_agent_pause_status(agent_id, project_id, team_id)
