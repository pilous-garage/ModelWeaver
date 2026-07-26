import sqlite3
import os
import signal
import shlex

from services._common import runtime_db_path
from services.api.router import register, register_dynamic, unregister
from services.service_manager import ServiceManager

_mgr = ServiceManager()

# ── Agent-as-Service helper ─────────────────────────────────────────────

def _agent_service_name(agent_name: str) -> str:
    return f"chat:{agent_name}"


def _list_agent_services():
    """Query chat agents from AgentsDB and return them as service-like dicts."""
    try:
        from modules.sql.sql_module import AgentsDB
        db = AgentsDB()
        rows = db.conn.execute(
            "SELECT name, role_type, status FROM agents WHERE role_type='chat' ORDER BY name"
        ).fetchall()
        now = int(__import__('time').time())
        return [{
            "name": _agent_service_name(r["name"]),
            "mode": "chat",
            "command": f"agent:chat:{r['name']}",
            "args": "",
            "status": r["status"] or "running",
            "pid": os.getpid(),
            "parent": "agent-as-service",
            "restart": 0,
            "restarts": 0,
            "last_exit": None,
            "started_at": now,
            "updated_at": now,
        } for r in rows]
    except Exception:
        return []

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
        services = [dict(r) for r in rows]
        # Merge ServiceManager entries
        existing = {s["name"] for s in services}
        for s in _mgr.list():
            sn = s["name"]
            if sn not in existing:
                services.append({
                    "name": sn,
                    "mode": s.get("spec", {}).get("mode", "chat"),
                    "command": f"agent:chat:{sn}",
                    "args": "",
                    "status": s.get("status", "running"),
                    "pid": s.get("pid", -1),
                    "parent": "agent-as-service",
                    "restart": 0,
                    "restarts": s.get("restarts", 0),
                    "last_exit": None,
                    "started_at": s.get("started_at", 0),
                    "updated_at": int(__import__('time').time()),
                })
                existing.add(sn)
        # Also merge raw agent-as-service entries (from AgentsDB fallback)
        for a in _list_agent_services():
            if a["name"] not in existing:
                services.append(a)
                existing.add(a["name"])
        return {"services": services, "count": len(services)}
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
    name = params.get("name", "")
    svc = _mgr.get(name)
    if svc:
        svc.restart()
        return {"status": "ok", "action": "restart", "name": name}
    return _service_command(name, "restart")


def op_service_stop(params):
    name = params.get("name", "")
    svc = _mgr.get(name)
    if svc:
        svc.stop()
        return {"status": "ok", "action": "stop", "name": name}
    return _service_command(name, "stop")


def op_service_register(params):
    """Register a service. Accepts:
      - {"from_file": "/path/to/service.yaml"}
      - {"agent_name": "x", "mode": "chat"} (legacy shorthand)
      - {"name": "x", "command": "...", "args": "...", "mode": "..."} (process)
    """
    from_file = params.get("from_file")
    if from_file:
        from services.service_spec import ServiceSpec
        spec = ServiceSpec.from_yaml(from_file)
        svc = _mgr.register(spec)
        return {"status": "ok", "name": spec.name, "svc_name": spec.svc_name, "routes": svc._routes}

    name = params.get("name")
    agent_name = params.get("agent_name")
    command = params.get("command", "")
    args = params.get("args", "")
    mode = params.get("mode", "once")

    if not name and not agent_name:
        return {"status": "error", "error": "name, agent_name ou from_file requis"}

    if agent_name:
        from services.service_spec import ServiceSpec, AgentSpec, LaunchSpec, EntrypointSpec, SupervisorSpec, HealthCheckSpec
        spec = ServiceSpec(
            name=agent_name,
            mode=agent_name,
            launch=LaunchSpec(mode="agent"),
            health=HealthCheckSpec(type="pid"),
            supervisor=SupervisorSpec(restart=False),
            agent=AgentSpec(role="chat", occupation="continue",
                            config={"description": f"Agent {agent_name}"}),
            entrypoints={
                "chat": EntrypointSpec(type="agent-chat", description=f"Chat avec {agent_name}"),
                "status": EntrypointSpec(type="inline", handler="""
def handler(params):
    return {"name": "chat:%s", "status": "running"}
""" % agent_name),
            },
        )
        svc = _mgr.register(spec)
        return {"status": "ok", "name": spec.name, "svc_name": spec.svc_name, "pid": svc.pid}

    if not name:
        return {"status": "error", "error": "name requis"}
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        conn.execute("INSERT OR REPLACE INTO services "
                      "(name, mode, command, args, status, pid, parent, restart, updated_at) "
                      "VALUES (?,?,?,?,'starting',-1,'runtime',1,strftime('%s','now'))",
                      (name, mode, command, args))
        conn.commit()
        proc = __import__('subprocess').Popen(
            shlex.split(command) if command else [__import__('sys').executable, "-c", "pass"],
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
    svc = _mgr.get(name)
    if svc:
        _mgr.unregister(name)
        return {"status": "ok", "name": name}
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        row = conn.execute("SELECT pid FROM services WHERE name=?", (name,)).fetchone()
        if row and row[0] and row[0] > 0:
            try:
                os.kill(row[0], signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
        conn.execute("DELETE FROM services WHERE name=?", (name,))
        conn.commit()
        conn.close()
        unregister(f"service/{name}/status")
        return {"status": "ok", "name": name}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Service Info & Routes ─────────────────────────────────────────────

def op_service_get(params):
    name = params.get("name", "")
    if not name:
        return {"status": "error", "error": "name requis"}
    svc = _mgr.get(name)
    if svc:
        return svc._status_handler({})
    try:
        db = runtime_db_path()
        conn = sqlite3.connect(str(db))
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT name, mode, command, args, status, pid, parent, "
            "restart, restarts, last_exit, started_at, updated_at "
            "FROM services WHERE name=?", (name,)
        ).fetchone()
        conn.close()
        if not row:
            return {"status": "error", "error": "service introuvable"}
        return dict(row)
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_service_routes(params):
    """Liste toutes les routes enregistrées du daemon, filtrées par service si name est fourni."""
    from services.api.router import ROUTES, STREAMING_ROUTES
    service_name = params.get("name", "")
    all_routes = []
    for r in ROUTES:
        if not service_name or r.startswith(f"service/{service_name}/") or r == f"service/{service_name}":
            all_routes.append(r)
    for r in STREAMING_ROUTES:
        if not service_name or r.startswith(f"service/{service_name}/") or r == f"service/{service_name}":
            all_routes.append(r + " (streaming)")
    if service_name:
        svc = _mgr.get(service_name)
        if svc:
            for ep_name in svc.spec.entrypoints:
                er = f"service/{svc.svc_name}/{ep_name}"
                if er not in all_routes:
                    all_routes.append(er)
    all_routes.sort()
    return {"routes": all_routes, "count": len(all_routes), "service": service_name or None}


# ── Route registration ─────────────────────────────────────────────────

register("service/list",       op_service_list)
register("service/get",        op_service_get)
register("service/routes",     op_service_routes)
register("service/restart",    op_service_restart)
register("service/stop",       op_service_stop)
register("service/register",   op_service_register)
register("service/unregister", op_service_unregister)
