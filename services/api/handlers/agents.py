import json

from services.api._shared import _get_mw
from services.api.router import register

# ── Agent DB (lazy singleton) ───────────────────────────────────────────

def _get_agent_db():
    from modules.sql.sql_module import AgentsDB
    d = getattr(_get_agent_db, "_db", None)
    if d is None:
        d = AgentsDB()
        _get_agent_db._db = d
    return d


# ── Agent Metrics ───────────────────────────────────────────────────────

def op_agent_metrics(params):
    db = _get_agent_db()
    agent_id = params.get("agent_id") if params else None
    if agent_id:
        row = db.conn.execute(
            "SELECT agent_id, total_tasks, total_tokens, failed_tasks, "
            "       total_runtime_ms, avg_latency_ms, last_updated "
            "FROM agent_metrics WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        if not row:
            return {"status": "error", "error": "aucune métrique pour cet agent"}
        return {"agent_id": agent_id, "metrics": dict(row)}
    else:
        rows = db.conn.execute(
            "SELECT agent_id, total_tasks, total_tokens, failed_tasks, "
            "       total_runtime_ms, avg_latency_ms, last_updated "
            "FROM agent_metrics ORDER BY agent_id"
        ).fetchall()
        return {"agents": [dict(r) for r in rows], "count": len(rows)}


def op_service_resources(params):
    try:
        import psutil
    except ImportError:
        return {"status": "error", "error": "psutil non disponible"}
    db = _get_mw()
    rows = db.conn.execute(
        "SELECT name, pid, status FROM services WHERE pid IS NOT NULL"
    ).fetchall()
    resources = []
    for r in rows:
        pid = r["pid"]
        try:
            proc = psutil.Process(pid)
            cpu = round(proc.cpu_percent(interval=0.1), 1)
            mem = proc.memory_info()
            rss_mb = round(mem.rss / (1024 * 1024), 1)
            resources.append({
                "name": r["name"],
                "pid": pid,
                "status": r["status"],
                "cpu_percent": cpu,
                "memory_rss_mb": rss_mb,
            })
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            resources.append({
                "name": r["name"],
                "pid": pid,
                "status": r["status"],
                "cpu_percent": None,
                "memory_rss_mb": None,
            })
    return {"services": resources, "count": len(resources)}


# ── Agent List / Get / Create / Delete / Execute / Launch ──────────────

def op_agent_list(_params):
    db = _get_agent_db()
    rows = db.conn.execute(
        "SELECT agent_id, name, ref, role_type, occupation, status, "
        "       created_at, last_active_at "
        "FROM agents ORDER BY name"
    ).fetchall()
    agents = [dict(r) for r in rows]
    for a in agents:
        rt = db.conn.execute(
            "SELECT thread_id, heartbeat_at, current_step FROM agent_runtime WHERE agent_id = ?",
            (a["agent_id"],)
        ).fetchone()
        if rt:
            a["running"] = True
            a["thread_id"] = rt["thread_id"]
            a["heartbeat"] = rt["heartbeat_at"]
            a["current_step"] = rt["current_step"]
        else:
            a["running"] = False
    return {"agents": agents, "count": len(agents)}


def op_agent_get(params):
    db = _get_agent_db()
    agent_id = params.get("agent_id")
    name = params.get("name")
    if agent_id:
        row = db.conn.execute("SELECT * FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    elif name:
        row = db.conn.execute("SELECT * FROM agents WHERE name = ?", (name,)).fetchone()
    else:
        return {"status": "error", "error": "agent_id ou name requis"}
    if not row:
        return {"status": "error", "error": "agent introuvable"}
    agent = dict(row)
    rt = db.conn.execute(
        "SELECT * FROM agent_runtime WHERE agent_id = ?", (agent["agent_id"],)
    ).fetchone()
    agent["runtime"] = dict(rt) if rt else None
    return {"agent": agent}


def op_agent_create(params):
    role = params.get("role")
    if not role:
        return {"status": "error", "error": "role requis"}
    from services.agent_manager.service import AgentManager
    mgr = AgentManager(db=_get_agent_db())
    name = mgr._make_agent_name(_get_agent_db().conn, role, params.get("name", ""))
    ref = f"agent:{name}"
    occupation = params.get("occupation", "noncontinue")
    config_json = json.dumps(params.get("config", {}))
    resources_json = json.dumps(params.get("resources", {}))
    variables_json = json.dumps(params.get("variables", {}))
    try:
        db_conn = _get_agent_db().conn
        db_conn.execute("""
            INSERT INTO agents (name, ref, role_type, occupation, config_json, resources_json, variables_json)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (name, ref, role, occupation, config_json, resources_json, variables_json))
        db_conn.commit()
        agent_id = db_conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()[0]
        from AgentFrameWork.agent_storage import AgentStorage
        AgentStorage(agent_id, db_conn).ensure()
        from services.audit import audit
        audit("agent.create", agent_id=agent_id, role=role, name=name, ok=True)
        return {"status": "ok", "agent_id": agent_id, "ref": ref}
    except Exception as e:
        from services.audit import audit
        audit("agent.create", role=role, ok=False, error=str(e))
        return {"status": "error", "error": str(e)}


def op_agent_delete(params):
    agent_id = params.get("agent_id")
    name = params.get("name")
    if not agent_id and not name:
        return {"status": "error", "error": "agent_id ou name requis"}
    db = _get_agent_db()
    if name:
        row = db.conn.execute("SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()
        if not row:
            return {"status": "error", "error": "agent introuvable"}
        agent_id = row["agent_id"]
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (agent_id,))
    db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (agent_id,))
    db.conn.commit()
    from AgentFrameWork.agent_storage import AgentStorage
    AgentStorage(agent_id, db.conn).destroy()
    from services.audit import audit
    audit("agent.delete", agent_id=agent_id, name=name, ok=True)
    return {"status": "ok", "agent_id": agent_id}


def op_agent_execute(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "execute",
                                 request=params.get("request", ""),
                                 provider_ref=params.get("provider_ref", ""),
                                 model_ref=params.get("model_ref", ""),
                                 entrypoint=params.get("entrypoint", "main"))


def op_agent_launch(params):
    create = op_agent_create(params)
    if create["status"] != "ok":
        return create
    agent_id = create["agent_id"]
    execute_params = {
        "agent_id": agent_id,
        "request": params.get("request", ""),
        "provider_ref": params.get("provider_ref", ""),
        "model_ref": params.get("model_ref", ""),
        "entrypoint": params.get("entrypoint", "main"),
    }
    exec_result = op_agent_execute(execute_params)
    return {"status": "ok", "agent_id": agent_id, "execute": exec_result}


# ── AFD Proxy ──────────────────────────────────────────────────────────

def op_agent_manager_status(_params):
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "manager_status")


def op_agent_evaluate(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "evaluate", resources=params.get("resources"))


def op_agent_admit(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "admit")


def op_agent_signal(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    stype = params.get("type")
    result = get_afd_client().call(ref, "signal", type=stype, payload=params.get("payload"))
    if stype in ("kill", "pause", "resume", "configure"):
        from services.audit import audit
        audit(f"agent.signal.{stype}", agent_id=agent_id, name=name,
              ok=result.get("status") == "ok", payload=params.get("payload"))
    return result


def op_agent_signals(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "signals", status=params.get("status"))


def op_agent_signal_ack(params):
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "signal/ack", signal_id=params.get("signal_id"))


def op_agent_signal_complete(params):
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "signal/complete", signal_id=params.get("signal_id"),
                                 result=params.get("result"))


def op_agent_stream(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    return get_afd_client().call(ref, "stream", seq=params.get("seq", 0))


def op_agent_spawn(params):
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "spawn", **params)


def op_agent_handoff(params):
    from services.api.afd_client import get_afd_client
    return get_afd_client().call(None, "handoff", **params)


def op_agent_capabilities(_params):
    from AgentFrameWork.router import capabilities_catalog as router_capabilities
    return router_capabilities()


# ── Route registration ─────────────────────────────────────────────────

register("agent/list",               op_agent_list)
register("agent/get",                op_agent_get)
register("agent/create",             op_agent_create)
register("agent/delete",             op_agent_delete)
register("agent/execute",            op_agent_execute)
register("agent/manager/status",     op_agent_manager_status)
register("agent/resources/evaluate", op_agent_evaluate)
register("agent/admit",              op_agent_admit)
register("agent/signal",             op_agent_signal)
register("agent/signals",            op_agent_signals)
register("agent/signal/ack",         op_agent_signal_ack)
register("agent/signal/complete",    op_agent_signal_complete)
register("agent/stream",             op_agent_stream)
register("agent/spawn",              op_agent_spawn)
register("agent/handoff",            op_agent_handoff)
register("agent/launch",             op_agent_launch)
register("agent/metrics",            op_agent_metrics)
register("service/resources",        op_service_resources)
