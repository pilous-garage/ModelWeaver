"""supervisor — skills de l'AGENT task_supervisor (ordonnancement des tâches).

L'agent supervisor (supervisor.agent.yaml) tourne par team : ses steps
appellent ces skills (assign/finalise/relay/make_respond) qui wrappent
TaskSupervisor.supervise_team — la mécanique unattributed→assigned→doing→
done→supervised. Aucun LLM : SQL + tri + signal (wakeup posé aux agents
capables quand une sub_task devient dispo).

Le workspace/team viennent des variables de l'agent (variables_json).
"""

from __future__ import annotations


def _scope(inputs: dict, home: str):
    workspace_id = (inputs.get("workspace_id") or "").strip()
    team_id = int(inputs.get("team_id", -1) or -1)
    # Résolution depuis le home de l'agent si non fourni.
    if not workspace_id:
        import re
        m = re.search(r"agent_home/(\d+)", home or "")
        if m:
            try:
                import json
                import sqlite3
                conn = sqlite3.connect(
                    "/home/pierreloup2/.modelweaver/agents.db")
                conn.row_factory = sqlite3.Row
                r = conn.execute(
                    "SELECT variables_json FROM agents WHERE agent_id = ?",
                    (int(m.group(1)),)).fetchone()
                conn.close()
                if r:
                    v = json.loads(r["variables_json"] or "{}")
                    workspace_id = v.get("workspace_id", "")
                    team_id = int(v.get("team_id", -1) or -1)
            except Exception:
                pass
    return workspace_id, team_id


def _supervise(workspace_id: str, team_id: int) -> dict:
    from services.task_supervisor.service import TaskSupervisor
    from modules.sqlite.workspace.workspace import WorkspaceDB
    if not workspace_id or team_id == -1:
        return {"ok": False, "error": "workspace_id + team_id requis",
                "released": 0, "supervised": 0, "created": 0}
    wdb = WorkspaceDB()
    try:
        res = TaskSupervisor(wdb).supervise_team(workspace_id, team_id)
        return {"ok": True, **res}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    finally:
        wdb.close()


def assign(inputs: dict, home: str) -> dict:
    """Ordonnancement : unattributed → attributed (attribution)."""
    ws, team = _scope(inputs, home)
    return _supervise(ws, team)


def finalise(inputs: dict, home: str) -> dict:
    """Finalise : tasks closes → supervised."""
    ws, team = _scope(inputs, home)
    return _supervise(ws, team)


def relay(inputs: dict, home: str) -> dict:
    """Relais : waiting_dependencies satisfaites → unattributed."""
    ws, team = _scope(inputs, home)
    return _supervise(ws, team)


def make_respond(inputs: dict, home: str) -> dict:
    """Crée la sub_task respond + réveille un prepare_response (signal)."""
    ws, team = _scope(inputs, home)
    return _supervise(ws, team)


__skills__ = ["assign", "finalise", "relay", "make_respond"]
