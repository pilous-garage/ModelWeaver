"""services.write — écritures services.db (services + ticks + runs).

L'API tick est celle du ServiceTicker (register/touch/log_run) : le ticker
se branche dessus SANS changer de contrat (les colonnes sont identiques).
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.base import Db


def register_service(d: Db, name: str, kind: str = "service",
                     module: str = "", description: str = "", cmd: str = "",
                     version: str = "") -> Dict[str, Any]:
    """Déclare un service (daemon, ticker_general, sqlite, watch…). Idempotent."""
    d._conn.execute(
        "INSERT INTO services (name, kind, module, description, cmd, version, "
        "status, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?, 'registered', datetime('now'), datetime('now')) "
        "ON CONFLICT(name) DO UPDATE SET kind=excluded.kind, "
        "module=excluded.module, description=excluded.description, "
        "cmd=excluded.cmd, version=excluded.version, "
        "updated_at=datetime('now')",
        (name, kind, module, description, cmd, version))
    d._conn.commit()
    return {"ok": True, "service": name}


def set_service_status(d: Db, name: str, status: str,
                       pid: Optional[int] = None,
                       port: Optional[int] = None) -> Dict[str, Any]:
    d._conn.execute(
        "UPDATE services SET status = ?, "
        "pid = COALESCE(?, pid), port = COALESCE(?, port), "
        "updated_at = datetime('now') WHERE name = ?",
        (status, pid, port, name))
    d._conn.commit()
    return {"ok": True, "service": name, "status": status}


def unregister_service(d: Db, name: str) -> Dict[str, Any]:
    d._conn.execute("DELETE FROM services WHERE name = ?", (name,))
    d._conn.commit()
    return {"ok": True, "service": name}


# ── tick ─────────────────────────────────────────────────────────

def _table_for(d: Db, svc_name: str, interval_s: Optional[float]) -> str:
    """Table par mode (permanent = 1s → service_ticks_secondes). Crée la
    ligne services si absente (un tick implique un service)."""
    mode = "permanent" if (interval_s is not None and interval_s <= 1.0) \
        else "ephémere"
    r = d._conn.execute(
        "SELECT 1 FROM services WHERE name = ?", (svc_name,)).fetchone()
    if not r:
        register_service(d, svc_name, kind="service",
                         description="(service de tick auto-déclaré)")
    return "service_ticks_secondes" if mode == "permanent" else "service_ticks"


def register_tick(d: Db, svc_name: str, interval_s: float = 60.0,
                  cmd: str = "") -> Dict[str, Any]:
    table = _table_for(d, svc_name, interval_s)
    if table == "service_ticks_secondes":
        d._conn.execute(
            f"INSERT INTO {table} (svc_name, cmd, enabled) VALUES (?,?,1) "
            f"ON CONFLICT(svc_name) DO UPDATE SET cmd=excluded.cmd, enabled=1",
            (svc_name, cmd))
    else:
        d._conn.execute(
            f"INSERT INTO {table} (svc_name, tick_interval_s, cmd, enabled) "
            f"VALUES (?,?,?,1) ON CONFLICT(svc_name) DO UPDATE SET "
            f"tick_interval_s=excluded.tick_interval_s, "
            f"cmd=excluded.cmd, enabled=1",
            (svc_name, float(interval_s), cmd))
    d._conn.commit()
    return {"ok": True, "service": svc_name, "table": table}


def unregister_tick(d: Db, svc_name: str) -> Dict[str, Any]:
    for t in ("service_ticks", "service_ticks_secondes"):
        d._conn.execute(f"DELETE FROM {t} WHERE svc_name = ?", (svc_name,))
    d._conn.commit()
    return {"ok": True, "service": svc_name}


def touch_tick(d: Db, svc_name: str, interval_s: float = 60.0,
               last_launch: float = 0, last_duration_s: float = 0,
               running: int = 0, enabled: int = 1) -> Dict[str, Any]:
    """Persiste l'état d'un tick (le cache local du ticker reste la source
    chaude ; ici on ne duplique que la supervision)."""
    table = _table_for(d, svc_name, interval_s)
    d._conn.execute(
        f"UPDATE {table} SET last_launch = ?, last_duration_s = ?, "
        f"running = ?, enabled = ? WHERE svc_name = ?",
        (last_launch, last_duration_s, running, enabled, svc_name))
    d._conn.commit()
    return {"ok": True, "service": svc_name}


def log_run(d: Db, svc_name: str, run_id: Optional[int] = None,
            thread_id: int = 0, started_at: float = 0,
            finished_at: Optional[float] = None, duration_s: float = 0,
            status: str = "running") -> Dict[str, Any]:
    """Démarre (run_id None → crée) ou clôt (run_id fourni → update) un run."""
    if run_id is None:
        cur = d._conn.execute(
            "INSERT INTO service_tick_runs (svc_name, thread_id, started_at, "
            "status) VALUES (?,?,?,?)",
            (svc_name, thread_id, started_at, status))
        d._conn.commit()
        return {"ok": True, "run_id": cur.lastrowid, "service": svc_name}
    d._conn.execute(
        "UPDATE service_tick_runs SET finished_at = ?, duration_s = ?, "
        "status = ? WHERE run_id = ?",
        (finished_at or 0, duration_s, status, run_id))
    d._conn.commit()
    return {"ok": True, "run_id": run_id}


def set_run_status(d: Db, run_id: int, status: str) -> None:
    d._conn.execute(
        "UPDATE service_tick_runs SET status = ? WHERE run_id = ?",
        (status, run_id))
    d._conn.commit()


def set_run_thread(d: Db, run_id: int, thread_id: int) -> None:
    d._conn.execute(
        "UPDATE service_tick_runs SET thread_id = ? WHERE run_id = ?",
        (thread_id, run_id))
    d._conn.commit()