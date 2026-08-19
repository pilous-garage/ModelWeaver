"""runtime.read — lectures runtime.db (état global + par service)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_state(d: Db) -> Optional[Dict[str, Any]]:
    r = d._conn.execute("SELECT * FROM runtime_state WHERE state_id = 1").fetchone()
    return dict(r) if r else None


def get_runtime(d: Db, svc_name: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM service_runtime WHERE svc_name = ?", (svc_name,)).fetchone()
    return dict(r) if r else None


def list_runtime(d: Db, status: str = "") -> List[Dict[str, Any]]:
    if status:
        rows = d._conn.execute(
            "SELECT * FROM service_runtime WHERE status = ? ORDER BY svc_name",
            (status,)).fetchall()
    else:
        rows = d._conn.execute(
            "SELECT * FROM service_runtime ORDER BY svc_name").fetchall()
    return [dict(r) for r in rows]


def next_ticks(d: Db, limit: int = 20) -> List[Dict[str, Any]]:
    """Les échéances à venir (tri next_tick ASC) — la file du ticker.
    next_tick=0 (service sans tick) est exclu."""
    rows = d._conn.execute(
        "SELECT svc_name, next_tick, last_tick, status FROM service_runtime "
        "WHERE status = 'running' AND next_tick > 0 "
        "ORDER BY next_tick LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]