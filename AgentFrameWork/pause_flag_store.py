"""PauseFlagStore — Stockage partagé du flag de pause par niveau.

Niveaux supportés :
  - project : tous les agents du même projet
  - team    : tous les agents de la même équipe
  - agent   : un agent spécifique

Le flag effectif pour un agent est la fusion OR de :
  project.paused AND team.paused AND agent.paused

Persistance : SQLite WAL partagé dans le home ModelWeaver.
Threadsafe : un seul writer par opération, lecture sans lock bloquant.

Mécanisme de notification :
  Un `threading.Condition` global est utilisé pour réveiller les agents
  en attente dès qu'un flag de pause change (pause ou resume).
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Optional

from services._common import mw_home


_DB_PATH = mw_home() / "pause_flags.db"
_WRITE_LOCK = threading.Lock()
_pause_cond = threading.Condition(_WRITE_LOCK)


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pause_flags (
            level   TEXT NOT NULL,
            ref     TEXT NOT NULL,
            paused  INTEGER NOT NULL DEFAULT 1,
            updated_at REAL NOT NULL,
            PRIMARY KEY (level, ref)
        )
    """)
    conn.commit()
    return conn


def set_paused(level: str, ref: str, paused: bool) -> None:
    """Positionne le flag de pause pour un niveau/ref donné.

    level : 'project' | 'team' | 'agent'
    ref   : identifiant de niveau (project_id, team_name, agent_id)
    """
    level = str(level).lower().strip()
    ref = str(ref).strip()
    if level not in {"project", "team", "agent"}:
        raise ValueError(f"Niveau de pause invalide: {level}")
    with _pause_cond:
        conn = _connect()
        try:
            conn.execute(
                """
                INSERT INTO pause_flags (level, ref, paused, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(level, ref) DO UPDATE SET
                    paused = excluded.paused,
                    updated_at = excluded.updated_at
                """,
                (level, ref, 1 if paused else 0, time.time()),
            )
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _pause_cond.notify_all()


def get_paused(level: str, ref: str) -> bool:
    """Retourne True si le flag est actif pour le niveau/ref donné."""
    level = str(level).lower().strip()
    ref = str(ref).strip()
    conn = _connect()
    try:
        row = conn.execute(
            "SELECT paused FROM pause_flags WHERE level = ? AND ref = ?",
            (level, ref),
        ).fetchone()
        return bool(row["paused"]) if row else False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def is_paused(project_id: Optional[str] = None,
              team_name: Optional[str] = None,
              agent_id: Optional[str | int] = None) -> bool:
    """Retourne True si l'entité doit être en pause.

    Le flag effectif est la fusion OR des niveaux fournis.
    Exemple :
      is_paused(project_id="mw", team_name="alpha", agent_id="agent_1")
    """
    checks = []
    if project_id:
        checks.append(get_paused("project", str(project_id)))
    if team_name:
        checks.append(get_paused("team", str(team_name)))
    if agent_id:
        checks.append(get_paused("agent", str(agent_id)))
    return any(checks)


def wait_for_resume(project_id: Optional[str] = None,
                    team_name: Optional[str] = None,
                    agent_id: Optional[str | int] = None,
                    poll_interval: float = 0.25,
                    timeout: Optional[float] = None) -> bool:
    """Bloque tant que le flag de pause est actif, puis se réveille à la
    première notification de changement.

    Retourne True si le flag a été désactivé, False si timeout atteint.
    """
    deadline = (time.time() + timeout) if timeout else None
    with _pause_cond:
        while is_paused(project_id=project_id, team_name=team_name, agent_id=agent_id):
            if deadline is not None:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return False
                _pause_cond.wait(timeout=min(poll_interval, remaining))
            else:
                _pause_cond.wait(timeout=poll_interval)
        return True


def wait_while_paused(project_id: Optional[str] = None,
                      team_name: Optional[str] = None,
                      agent_id: Optional[str | int] = None,
                      poll_interval: float = 0.25) -> None:
    """Bloque tant que le flag de pause est actif.

    Utilisé par le FSM et le bridge pour mettre en attente les streams.
    """
    while is_paused(project_id=project_id, team_name=team_name, agent_id=agent_id):
        with _pause_cond:
            _pause_cond.wait(timeout=poll_interval)


def clear_all() -> None:
    """Réinitialise tous les flags (principalement pour tests)."""
    with _pause_cond:
        conn = _connect()
        try:
            conn.execute("DELETE FROM pause_flags")
            conn.commit()
        finally:
            try:
                conn.close()
            except Exception:
                pass
        _pause_cond.notify_all()
