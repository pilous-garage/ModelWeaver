"""Service Supervisor — process lifecycle management for agent-as-service.

Follows the same pattern as the usage_collector supervisor:
  - Background daemon thread
  - Periodic poll loop
  - Reads service_commands table for start/stop/restart
  - Monitors running services and respawns on crash

Compatible with the Rust supervisor (same DB schema, same service_commands table).
This is a fallback for when the Rust supervisor is not present.
"""

import os
import sys
import signal
import time
import sqlite3
import threading
import subprocess
from pathlib import Path
from typing import Optional

from services._common import runtime_db_path


SERVICE_COMMANDS_SCHEMA = """
    CREATE TABLE IF NOT EXISTS service_commands (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT,
        action TEXT,
        created_at INTEGER DEFAULT (strftime('%s','now')),
        executed INTEGER DEFAULT 0
    )
"""

SERVICES_SCHEMA = """
    CREATE TABLE IF NOT EXISTS services (
        name TEXT PRIMARY KEY,
        mode TEXT,
        command TEXT,
        args TEXT,
        status TEXT,
        pid INTEGER,
        parent TEXT,
        restart INTEGER,
        restarts INTEGER DEFAULT 0,
        last_exit INTEGER,
        started_at INTEGER,
        updated_at INTEGER DEFAULT (strftime('%s','now'))
    )
"""


def _ensure_schema(conn: sqlite3.Connection):
    conn.execute(SERVICE_COMMANDS_SCHEMA)
    conn.execute(SERVICES_SCHEMA)
    conn.commit()


def _open_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(runtime_db_path()))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    _ensure_schema(conn)
    return conn


def _process_command(conn: sqlite3.Connection, row: sqlite3.Row):
    name = row["name"]
    action = row["action"]
    cmd_id = row["id"]

    if action == "start":
        svc = conn.execute(
            "SELECT command, args FROM services WHERE name=? AND status IN ('stopped','exited','crashed')",
            (name,)
        ).fetchone()
        if svc:
            _spawn_service(conn, name, svc["command"], svc["args"])
    elif action == "stop":
        _stop_service(conn, name)
    elif action == "restart":
        _stop_service(conn, name)
        svc = conn.execute(
            "SELECT command, args FROM services WHERE name=?", (name,)
        ).fetchone()
        if svc:
            time.sleep(0.5)
            _spawn_service(conn, name, svc["command"], svc["args"])
    elif action == "kill":
        row_svc = conn.execute(
            "SELECT pid FROM services WHERE name=?", (name,)
        ).fetchone()
        if row_svc and row_svc["pid"] and row_svc["pid"] > 0:
            try:
                os.kill(row_svc["pid"], signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        conn.execute("UPDATE services SET status='killed', pid=-1, updated_at=strftime('%s','now') WHERE name=?", (name,))
        conn.commit()

    conn.execute("UPDATE service_commands SET executed=1 WHERE id=?", (cmd_id,))
    conn.commit()


def _stop_service(conn: sqlite3.Connection, name: str):
    row = conn.execute("SELECT pid, status FROM services WHERE name=?", (name,)).fetchone()
    if not row:
        return
    pid = row["pid"]
    if pid and pid > 0:
        try:
            os.kill(pid, signal.SIGTERM)
            for _ in range(25):
                try:
                    os.kill(pid, 0)
                    time.sleep(0.1)
                except OSError:
                    break
            else:
                os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
    conn.execute(
        "UPDATE services SET status='stopped', pid=-1, last_exit=strftime('%s','now'), "
        "updated_at=strftime('%s','now') WHERE name=?",
        (name,)
    )
    conn.commit()


def _spawn_service(conn: sqlite3.Connection, name: str, command: str, args: str):
    import shlex
    if command:
        cmd_parts = shlex.split(command)
    else:
        cmd_parts = [sys.executable, "-c", "pass"]
    if args:
        cmd_parts.extend(shlex.split(args))
    try:
        proc = subprocess.Popen(
            cmd_parts,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except Exception:
        conn.execute(
            "UPDATE services SET status='failed', updated_at=strftime('%s','now') WHERE name=?",
            (name,)
        )
        conn.commit()
        return
    pid = proc.pid
    conn.execute(
        "UPDATE services SET pid=?, status='running', started_at=strftime('%s','now'), "
        "updated_at=strftime('%s','now') WHERE name=?",
        (pid, name)
    )
    conn.commit()


def _is_really_alive(pid: int) -> bool:
    """Check if a PID is alive AND not a zombie."""
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            state = f.read().split()[2]
            if state == 'Z':
                return False
    except (OSError, IndexError):
        pass
    return True


def _monitor_services(conn: sqlite3.Connection):
    rows = conn.execute(
        "SELECT name, pid, command, args, restarts, restart FROM services WHERE status='running' AND pid IS NOT NULL AND pid > 0"
    ).fetchall()
    for r in rows:
        pid = r["pid"]
        alive = _is_really_alive(pid)
        if not alive:
            max_restarts = r["restart"] or 0
            cur_restarts = r["restarts"] or 0
            if max_restarts > 0 and cur_restarts >= max_restarts:
                conn.execute(
                    "UPDATE services SET status='exhausted', last_exit=strftime('%s','now'), "
                    "updated_at=strftime('%s','now') WHERE name=?",
                    (r["name"],)
                )
                conn.commit()
                continue
            conn.execute(
                "UPDATE services SET status='restarting', restarts=restarts+1, "
                "updated_at=strftime('%s','now') WHERE name=?",
                (r["name"],)
            )
            conn.commit()
            _spawn_service(conn, r["name"], r["command"], r["args"])


def _sync_agent_services(conn: sqlite3.Connection):
    """Re-insert agent-as-service entries if the Rust supervisor cleaned them."""
    try:
        from modules.sql.sql_module import AgentsDB
        adb = AgentsDB()
        rows = adb.conn.execute(
            "SELECT name FROM agents WHERE role_type='chat'"
        ).fetchall()
        existing = {r["name"] for r in conn.execute("SELECT name FROM services").fetchall()}
        now = int(__import__('time').time())
        for r in rows:
            svc_name = f"chat:{r['name']}"
            if svc_name not in existing:
                conn.execute(
                    "INSERT OR REPLACE INTO services "
                    "(name, mode, command, args, status, pid, parent, restart, started_at) "
                    "VALUES (?,?,?,?,'running',?,'agent-as-service',0,?)",
                    (svc_name, "chat", f"agent:chat:{r['name']}", "",
                     __import__('os').getpid(), now)
                )
        conn.commit()
    except Exception:
        pass


def _supervisor_tick(conn: sqlite3.Connection):
    try:
        rows = conn.execute(
            "SELECT id, name, action FROM service_commands WHERE executed=0 ORDER BY id"
        ).fetchall()
        for row in rows:
            _process_command(conn, row)
    except Exception:
        pass
    try:
        _monitor_services(conn)
    except Exception:
        pass
    try:
        _sync_agent_services(conn)
    except Exception:
        pass


def _supervisor_loop(interval: float = 5.0):
    while True:
        try:
            conn = _open_db()
            try:
                _supervisor_tick(conn)
            finally:
                conn.close()
        except Exception:
            pass
        time.sleep(interval)


def start_supervisor(interval: float = 5.0) -> threading.Thread:
    t = threading.Thread(
        target=_supervisor_loop,
        args=(interval,),
        daemon=True,
        name="service-supervisor",
    )
    t.start()
    return t
