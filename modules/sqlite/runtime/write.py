"""runtime.write — écritures runtime.db (état global + par service)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.base import Db


def set_state(d: Db, pid: int = 0, host: str = "", mw_version: str = "",
              boot_at: str = "", mw_home: str = "",
              python_version: str = "") -> Dict[str, Any]:
    """Boot/refresh de l'état global (une seule ligne, state_id=1)."""
    d._conn.execute(
        "INSERT INTO runtime_state (state_id, pid, host, mw_version, boot_at, "
        "mw_home, python_version, updated_at) VALUES (1,?,?,?,?,?,?, "
        "datetime('now')) ON CONFLICT(state_id) DO UPDATE SET pid=excluded.pid, "
        "host=excluded.host, mw_version=excluded.mw_version, "
        "boot_at=excluded.boot_at, mw_home=excluded.mw_home, "
        "python_version=excluded.python_version, updated_at=datetime('now')",
        (pid, host, mw_version, boot_at, mw_home, python_version))
    d._conn.commit()
    return {"ok": True, "pid": pid}


def touch(d: Db, svc_name: str, pid: Optional[int] = None,
          port: Optional[int] = None, status: Optional[str] = None,
          last_tick: Optional[float] = None,
          next_tick: Optional[float] = None,
          last_duration_s: Optional[float] = None,
          ticks_count: Optional[int] = None) -> Dict[str, Any]:
    """Upsert de l'état runtime d'un service (le ticker appelle touch à
    chaque flush : last_tick/next_tick). Champs None = inchangés."""
    d._conn.execute(
        "INSERT INTO service_runtime (svc_name, pid, port, status, last_tick, "
        "next_tick, last_duration_s, ticks_count, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?, datetime('now')) "
        "ON CONFLICT(svc_name) DO UPDATE SET "
        "pid=COALESCE(excluded.pid, pid), port=COALESCE(excluded.port, port), "
        "status=COALESCE(excluded.status, status), "
        "last_tick=COALESCE(excluded.last_tick, last_tick), "
        "next_tick=COALESCE(excluded.next_tick, next_tick), "
        "last_duration_s=COALESCE(excluded.last_duration_s, last_duration_s), "
        "ticks_count=COALESCE(excluded.ticks_count, ticks_count), "
        "updated_at=datetime('now')",
        (svc_name, pid, port, status, last_tick, next_tick,
         last_duration_s, ticks_count))
    d._conn.commit()
    return {"ok": True, "service": svc_name}


def stop(d: Db, svc_name: str, status: str = "stopped") -> Dict[str, Any]:
    return touch(d, svc_name, status=status)