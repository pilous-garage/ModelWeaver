import sqlite3
import sys
import signal
import os

from services._common import runtime_db_path
from services.api.router import register, register_dynamic, unregister

# ── Service Manager ─────────────────────────────────────────────────────

def op_service_list(_params):
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT name, mode, command, args, status, pid, parent, "
            "restart, restarts, last_exit, started_at, updated_at "
            "FROM services ORDER BY started_at"
        ).fetchall()
        conn.close()
        return {"services": [dict(r) for r in rows], "count": len(rows)}
    except Exception as e:
        return {"services": [], "count": 0, "error": str(e)}


def _service_command(name: str, action: str):
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        conn.execute("CREATE TABLE IF NOT EXISTS service_commands "
                      "(id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, action TEXT, "
                      "created_at INTEGER DEFAULT (strftime('%s','now')), executed INTEGER DEFAULT 0)")
        conn.execute("INSERT INTO service_commands (name, action) VALUES (?, ?)", (name, action))
        conn.commit()
        conn.close()
        return {"status": "ok", "action": action, "name": name}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_service_restart(params):
    return _service_command(params.get("name", ""), "restart")


def op_service_stop(params):
    return _service_command(params.get("name", ""), "stop")


def op_service_register(params):
    name = params.get("name")
    agent_id = params.get("agent_id")
    command = params.get("command", "")
    args = params.get("args", "")
    mode = params.get("mode", "once")
    if not name or not agent_id:
        return {"status": "error", "error": "name et agent_id requis"}
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT OR REPLACE INTO services "
                      "(name, mode, command, args, status, pid, parent, restart, updated_at) "
                      "VALUES (?,?,?,?,'starting',-1,'agent-as-service',1,strftime('%s','now'))",
                      (name, mode, command, args))
        conn.commit()
        proc = __import__('subprocess').Popen(
            command.split() if command else [sys.executable, "-c", "pass"],
            stdout=__import__('subprocess').DEVNULL, stderr=__import__('subprocess').DEVNULL,
        )
        pid = proc.pid
        conn.execute("UPDATE services SET pid=?, status='running', started_at=strftime('%s','now') WHERE name=?",
                      (pid, name))
        conn.commit()
        conn.close()
        register_dynamic(f"service/{name}/status", lambda p, _n=name: {"name": _n, "running": True})
        return {"status": "ok", "name": name, "pid": pid}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_service_unregister(params):
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT pid FROM services WHERE name=? AND parent='agent-as-service'", (name,)).fetchone()
        if row and row[0] and row[0] > 0:
            try:
                os.kill(row[0], signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        conn.execute("DELETE FROM services WHERE name=? AND parent='agent-as-service'", (name,))
        conn.commit()
        conn.close()
        unregister(f"service/{name}/status")
        return {"status": "ok", "name": name}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Route registration ─────────────────────────────────────────────────

register("service/list",       op_service_list)
register("service/restart",    op_service_restart)
register("service/stop",       op_service_stop)
register("service/register",   op_service_register)
register("service/unregister", op_service_unregister)
