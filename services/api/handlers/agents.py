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
    # Source de vérité des tokens/requêtes : model_call_log (appels LLM réels),
    # agrégé par agent_id. agent_metrics ne couvre que le bridge direct (Phase 1)
    # — les agents greedy (FSM) ne l'alimentent pas → tokens ~0. On agrège donc
    # depuis model_call_log et on fusionne avec agent_metrics (tasks/failed).
    try:
        from services.api._shared import _get_cat
        cat = _get_cat()
        def _call_log_stats(aid):
            rows = cat.conn.execute("""
                SELECT COUNT(*) AS requests,
                       SUM(tokens_in) AS tokens_in,
                       SUM(tokens_out) AS tokens_out,
                       SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS errors
                FROM model_call_log
                WHERE agent_id = ?
            """, (str(aid),)).fetchone()
            return dict(rows) if rows else {}
    except Exception:
        def _call_log_stats(aid):
            return {}
    if agent_id:
        row = db.conn.execute(
            "SELECT agent_id, total_tasks, total_tokens, failed_tasks, "
            "       total_runtime_ms, avg_latency_ms, last_updated "
            "FROM agent_metrics WHERE agent_id = ?", (agent_id,)
        ).fetchone()
        cl = _call_log_stats(agent_id)
        base = dict(row) if row else {"agent_id": agent_id, "total_tasks": 0,
                                      "total_tokens": 0, "failed_tasks": 0,
                                      "total_runtime_ms": 0, "avg_latency_ms": 0}
        return {"agent_id": agent_id, "metrics": {
            **base,
            "requests": cl.get("requests") or 0,
            "total_tokens": (base.get("total_tokens") or 0)
                            + (cl.get("tokens_in") or 0) + (cl.get("tokens_out") or 0),
            "tokens_in": cl.get("tokens_in") or 0,
            "tokens_out": cl.get("tokens_out") or 0,
            "errors": cl.get("errors") or 0,
        }}
    else:
        rows = db.conn.execute(
            "SELECT agent_id, total_tasks, total_tokens, failed_tasks, "
            "       total_runtime_ms, avg_latency_ms, last_updated "
            "FROM agent_metrics ORDER BY agent_id"
        ).fetchall()
        out = []
        for r in rows:
            cl = _call_log_stats(r["agent_id"])
            out.append({
                **dict(r),
                "requests": cl.get("requests") or 0,
                "total_tokens": (r["total_tokens"] or 0)
                                + (cl.get("tokens_in") or 0) + (cl.get("tokens_out") or 0),
                "tokens_in": cl.get("tokens_in") or 0,
                "tokens_out": cl.get("tokens_out") or 0,
                "errors": cl.get("errors") or 0,
            })
        return {"agents": out, "count": len(out)}


def op_service_resources(params):
    try:
        import psutil
    except ImportError:
        return {"status": "error", "error": "psutil non disponible"}
    import sqlite3
    from services._common import runtime_db_path
    from services.api.handlers.services_handlers import _list_agent_services
    conn = sqlite3.connect(str(runtime_db_path()))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT name, pid, status FROM services WHERE pid IS NOT NULL"
        ).fetchall()
    except Exception:
        return {"services": [], "count": 0}
    finally:
        conn.close()
    # Merge agent-as-service (chat agents from AgentsDB)
    existing = {r["name"] for r in rows}
    agent_services = [a for a in _list_agent_services() if a["name"] not in existing]
    all_services = list(rows) + agent_services
    resources = []
    for r in all_services:
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


def op_agent_stop(params):
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    db = _get_agent_db()
    if agent_id:
        row = db.conn.execute("SELECT agent_id, status FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    elif name:
        row = db.conn.execute("SELECT agent_id, status FROM agents WHERE name = ?", (name,)).fetchone()
        if row:
            agent_id = row["agent_id"]
    if not row:
        return {"status": "error", "error": "agent introuvable"}
    from services.api.afd_client import get_afd_client
    get_afd_client().call(agent_id, "signal", type="kill")
    db.conn.execute(
        "UPDATE agents SET status = 'STOPPED', last_active_at = datetime('now') WHERE agent_id = ?",
        (agent_id,))
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (agent_id,))
    db.conn.commit()
    from services.audit import audit
    audit("agent.stop", agent_id=agent_id, name=name, ok=True)
    return {"status": "ok", "agent_id": agent_id, "action": "stop"}


def op_agent_restart(params):
    from services.api.afd_client import get_afd_client
    agent_id = params.get("agent_id")
    name = params.get("name")
    ref = agent_id or name
    if not ref:
        return {"status": "error", "error": "agent_id ou name requis"}
    db = _get_agent_db()
    if agent_id:
        row = db.conn.execute("SELECT agent_id, name, config_json, resources_json FROM agents WHERE agent_id = ?", (agent_id,)).fetchone()
    elif name:
        row = db.conn.execute("SELECT agent_id, name, config_json, resources_json FROM agents WHERE name = ?", (name,)).fetchone()
        if row:
            agent_id = row["agent_id"]
    if not row:
        return {"status": "error", "error": "agent introuvable"}
    get_afd_client().call(agent_id, "signal", type="kill")
    db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (agent_id,))
    db.conn.commit()
    exec_result = get_afd_client().call(agent_id, "execute",
                                        request=params.get("request", ""),
                                        provider_ref=params.get("provider_ref", ""),
                                        model_ref=params.get("model_ref", ""),
                                        entrypoint=params.get("entrypoint", "main"))
    from services.audit import audit
    audit("agent.restart", agent_id=agent_id, name=row["name"], ok=True)
    return {"status": "ok", "agent_id": agent_id, "action": "restart", "execute": exec_result}


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


def op_agent_taskflow(params):
    """Graphe de circulation des tâches (Taskflow) du swarm.

    Pour chaque type d'agent : les types de tâches qu'il CONSOMME (role_type →
    ROLE_TO_TASK, avec hiérarchie senior/mid/junior) et qu'il GÉNÈRE (champ
    `generates` du .agent.yaml). Paramètre optionnel team (ex. "team:swarm-...").
    """
    from services.swarm_topology import build_taskflow
    team = params.get("team") or params.get("team_name") or ""
    return build_taskflow(team)


# ── Route registration ─────────────────────────────────────────────────

def op_agent_list_by_team(_params):
    """Liste les agents groupés par équipe (préfixe team:XXX/)."""
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

    import re
    teams = {}
    standalone = []
    for a in agents:
        m = re.match(r"^team:([^/]+)/", a["name"])
        if m:
            team_name = m.group(1)
            teams.setdefault(team_name, []).append(a)
        else:
            standalone.append(a)

    from services.team_manager import TeamManager
    mgr = TeamManager()
    team_info = {}
    for tname in teams:
        t = mgr.get(f"team:{tname}")
        if t:
            team_info[tname] = t.status_info()

    return {
        "teams": {k: {"agents": v, "team_info": team_info.get(k)} for k, v in teams.items()},
        "standalone": standalone,
        "team_count": len(teams),
        "standalone_count": len(standalone),
        "total": len(agents),
    }


def op_agent_topology(_params):
    """Graphe de topologie : équipes (leader → membres), successeurs, handoffs.
    Nœuds : agents avec status/running/preemptible. Liens : team_lead, member, successor."""
    db = _get_agent_db()
    rows = db.conn.execute(
        "SELECT agent_id, name, ref, role_type, occupation, status, resources_json, "
        "       successor_id, created_at, last_active_at "
        "FROM agents ORDER BY name"
    ).fetchall()
    agents = [dict(r) for r in rows]

    running_ids = set()
    for a in agents:
        rt = db.conn.execute(
            "SELECT current_step FROM agent_runtime WHERE agent_id = ?",
            (a["agent_id"],)
        ).fetchone()
        if rt:
            running_ids.add(a["agent_id"])
            a["running"] = True
            a["current_step"] = rt["current_step"]
        else:
            a["running"] = False

    def _parse_resources(raw):
        try:
            return json.loads(raw) if raw else {}
        except Exception:
            return {}

    for a in agents:
        res = _parse_resources(a.pop("resources_json", None))
        a["preemptible"] = bool(res.get("preemptible", False))
        a["priority"] = res.get("priority", 0)
        a["llm"] = res.get("llm")

    by_name = {a["name"]: a for a in agents}
    nodes, edges = [], []
    for a in agents:
        nodes.append({
            "id": a["agent_id"], "name": a["name"], "ref": a["ref"],
            "role_type": a["role_type"], "occupation": a["occupation"],
            "status": a["status"], "running": a["running"],
            "current_step": a.get("current_step"), "preemptible": a["preemptible"],
            "priority": a["priority"], "llm": a["llm"],
            "successor_id": a["successor_id"],
        })

    for a in agents:
        if a["successor_id"]:
            edges.append({"from": a["agent_id"], "to": a["successor_id"], "type": "successor"})

    import re
    teams = {}
    for a in agents:
        m = re.match(r"^team:([^/]+)/", a["name"])
        if m:
            teams.setdefault(m.group(1), []).append(a)

    from services.team_manager import TeamManager
    mgr = TeamManager()
    for tname, members in teams.items():
        t = mgr.get(f"team:{tname}")
        leader_id = None
        if t and t.team_leader_agent_id:
            leader_id = t.team_leader_agent_id
            leader = by_name.get(t.spec.team_leader.agent_name) if t.spec.team_leader else None
            edges.append({
                "from": None, "to": leader_id, "type": "team_lead",
                "team": tname, "topology": t.spec.topology,
                "leader_name": leader["name"] if leader else None,
            })
        for m in members:
            if leader_id is not None and m["agent_id"] != leader_id:
                edges.append({"from": leader_id, "to": m["agent_id"], "type": "member", "team": tname})

    return {
        "ok": True,
        "nodes": nodes,
        "edges": edges,
        "teams": list(teams.keys()),
        "count": len(nodes),
    }


register("agent/list",               op_agent_list)
register("agent/list-by-team",       op_agent_list_by_team)
register("agent/topology",           op_agent_topology)
register("agent/taskflow",           op_agent_taskflow)
register("capabilities",             op_agent_capabilities)
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
register("agent/stop",               op_agent_stop)
register("agent/restart",            op_agent_restart)
register("agent/spawn",              op_agent_spawn)
register("agent/handoff",            op_agent_handoff)
register("agent/launch",             op_agent_launch)
register("agent/metrics",            op_agent_metrics)
register("service/resources",        op_service_resources)
