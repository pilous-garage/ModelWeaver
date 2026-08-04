"""PauseFlag — Stockage partagé du flag de pause par niveau (projet / team / agent).

API de lecture/écriture thread-safe, reposant sur SQLite WAL.
Les niveaux sont hiérarchiques : un flag projet ou team écrase un flag agent
lorsqu'il est activé. Un flag agent actif est ignoré si un flag parent est
également actif.

Conventions :
- scope : "project" | "team" | "agent"
- scope_id : identifiant logique selon le scope
  - project : project_id
  - team : team_id
  - agent : agent_id
- state : "paused" | "running"
- reason : texte libre (optionnel)
- updated_at : epoch float
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home

logger = logging.getLogger("modelweaver.control.pause_flag")

_DEFAULT_DB = mw_home() / "pause_flags.db"


class PauseFlagStore:
    """Stockage SQLite WAL des flags de pause."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        path = Path(db_path) if db_path else _DEFAULT_DB
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._write_lock = threading.Lock()
        self._conn: Optional[sqlite3.Connection] = None
        self._init_db()

    # ── Schéma ─────────────────────────────────────────────────────

    def _init_db(self) -> None:
        url = f"file:{self._path}?mode=rwc"
        self._conn = sqlite3.connect(url, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pause_flags (
                scope      TEXT NOT NULL CHECK (scope IN ('project','team','agent')),
                scope_id   TEXT NOT NULL,
                state      TEXT NOT NULL CHECK (state IN ('paused','running')),
                reason     TEXT,
                updated_at REAL NOT NULL,
                PRIMARY KEY (scope, scope_id)
            )
            """
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_pause_scope_id ON pause_flags (scope, scope_id)"
        )
        self._conn.commit()

    # ── Helpers ────────────────────────────────────────────────────

    def _now(self) -> float:
        return time.time()

    # ── API publique ───────────────────────────────────────────────

    def set_flag(
        self,
        scope: str,
        scope_id: str,
        state: str,
        reason: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Positionne un flag de pause pour un scope donné."""
        if scope not in {"project", "team", "agent"}:
            raise ValueError(f"scope invalide : {scope}")
        if state not in {"paused", "running"}:
            raise ValueError(f"state invalide : {state}")

        row = {
            "scope": scope,
            "scope_id": str(scope_id),
            "state": state,
            "reason": reason,
            "updated_at": self._now(),
        }
        with self._write_lock:
            self._conn.execute(
                """
                INSERT INTO pause_flags (scope, scope_id, state, reason, updated_at)
                VALUES (:scope, :scope_id, :state, :reason, :updated_at)
                ON CONFLICT(scope, scope_id) DO UPDATE SET
                    state = excluded.state,
                    reason = excluded.reason,
                    updated_at = excluded.updated_at
                """,
                row,
            )
            self._conn.commit()
        logger.debug("pause flag set: %s/%s -> %s", scope, scope_id, state)
        return row

    def get_flag(self, scope: str, scope_id: str) -> Optional[Dict[str, Any]]:
        """Retourne le flag pour un scope donné, ou None."""
        if scope not in {"project", "team", "agent"}:
            raise ValueError(f"scope invalide : {scope}")
        cur = self._conn.execute(
            "SELECT * FROM pause_flags WHERE scope = ? AND scope_id = ?",
            (scope, str(scope_id)),
        )
        row = cur.fetchone()
        return dict(row) if row else None

    def effective_state(
        self,
        project_id: Optional[str] = None,
        team_id: Optional[str] = None,
        agent_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retourne l'état effectif de pause en tenant compte de la hiérarchie.

        Priorité : project > team > agent.
        Si un flag parent est paused, il écrase les flags enfants.
        """
        project_flag = self.get_flag("project", str(project_id)) if project_id else None
        team_flag = self.get_flag("team", str(team_id)) if team_id else None
        agent_flag = self.get_flag("agent", str(agent_id)) if agent_id else None

        active = None
        for flag in (project_flag, team_flag, agent_flag):
            if flag and flag["state"] == "paused":
                active = flag
                break

        return {
            "paused": bool(active),
            "state": active["state"] if active else "running",
            "reason": active.get("reason") if active else None,
            "scope": active["scope"] if active else None,
            "scope_id": active.get("scope_id") if active else None,
            "project_flag": project_flag,
            "team_flag": team_flag,
            "agent_flag": agent_flag,
        }

    def clear_flag(self, scope: str, scope_id: str) -> None:
        """Supprime un flag de pause (équivalent à set_flag(..., 'running'))."""
        self.set_flag(scope, scope_id, "running", reason="cleared")

    def clear_all(self, scope: Optional[str] = None) -> int:
        """Supprime tous les flags, ou uniquement ceux d'un scope donné.

        Retourne le nombre de lignes supprimées.
        """
        with self._write_lock:
            if scope:
                cur = self._conn.execute(
                    "DELETE FROM pause_flags WHERE scope = ?", (scope,)
                )
            else:
                cur = self._conn.execute("DELETE FROM pause_flags")
            self._conn.commit()
            return cur.rowcount

    def list_flags(self, scope: Optional[str] = None) -> List[Dict[str, Any]]:
        """Liste les flags, éventuellement filtrés par scope."""
        if scope:
            cur = self._conn.execute(
                "SELECT * FROM pause_flags WHERE scope = ? ORDER BY updated_at DESC",
                (scope,),
            )
        else:
            cur = self._conn.execute(
                "SELECT * FROM pause_flags ORDER BY scope, updated_at DESC"
            )
        return [dict(r) for r in cur.fetchall()]

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None


# Singleton
_store: Optional[PauseFlagStore] = None
_store_lock = threading.Lock()


def get_pause_store(db_path: Optional[str] = None) -> PauseFlagStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = PauseFlagStore(db_path=db_path)
        return _store
