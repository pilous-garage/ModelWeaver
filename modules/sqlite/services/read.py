"""services.read — lectures services.db (services + ticks + runs)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db


def get_service(d: Db, name: str) -> Optional[Dict[str, Any]]:
    r = d._conn.execute(
        "SELECT * FROM services WHERE name = ?", (name,)).fetchone()
    return dict(r) if r else None


def list_services(d: Db, kind: str = "", status: str = "") -> List[Dict[str, Any]]:
    q = "SELECT * FROM services WHERE 1=1"
    p: list = []
    if kind:
        q += " AND kind = ?"
        p.append(kind)
    if status:
        q += " AND status = ?"
        p.append(status)
    q += " ORDER BY name"
    return [dict(r) for r in d._conn.execute(q, p).fetchall()]


def get_tick(d: Db, svc_name: str) -> Optional[Dict[str, Any]]:
    for t in ("service_ticks", "service_ticks_secondes"):
        r = d._conn.execute(
            f"SELECT * FROM {t} WHERE svc_name = ?", (svc_name,)).fetchone()
        if r:
            out = dict(r)
            out["mode"] = "permanent" if t == "service_ticks_secondes" \
                else "ephémere"
            return out
    return None


def list_ticks(d: Db, enabled_only: bool = True) -> List[Dict[str, Any]]:
    """Fusion éphémère + permanent avec les MÊMES colonnes (SQLite exige
    l'uniformité pour UNION ALL)."""
    cond = "WHERE enabled = 1" if enabled_only else ""
    q = (f"SELECT 'ephémere' AS mode, tick_id, svc_name, cmd, last_launch, "
         f"last_duration_s, running, enabled, created_at FROM service_ticks "
         f"{cond} UNION ALL "
         f"SELECT 'permanent' AS mode, tick_id, svc_name, cmd, last_launch, "
         f"last_duration_s, running, enabled, created_at "
         f"FROM service_ticks_secondes {cond} ORDER BY svc_name")
    return [dict(r) for r in d._conn.execute(q).fetchall()]


def list_runs(d: Db, svc_name: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    if svc_name:
        rows = d._conn.execute(
            "SELECT * FROM service_tick_runs WHERE svc_name = ? "
            "ORDER BY run_id DESC LIMIT ?", (svc_name, limit)).fetchall()
    else:
        rows = d._conn.execute(
            "SELECT * FROM service_tick_runs ORDER BY run_id DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(r) for r in rows]