"""reset_variable_after_change_task — purge les variables de run du cycle
précédent quand l'agent change de tâche (pas une reprise doing).

Le FSM stocke les variables de run dans la table agents.variables_json. Quand
un greedy prend une NOUVELLE tâche (resumed=False), les variables du cycle
précédent (repo_eff, work_out, respond_out, review_out, _conv_id, erreurs LLM)
doivent être effacées — sinon le nouveau travail hérite de l'ancien contexte.
"""

import json
import re

# Clés de cycle par défaut à purger (résidus d'un run précédent sur une autre
# tâche). Les clés d'IDENTITÉ (agent_id, home, workspace_id, team_id,
# project_id) ne sont JAMAIS purgées.
_DEFAULT_KEYS = (
    "repo_eff", "work_out", "respond_out", "review_out", "merge_out",
    "_conv_id", "_last_call_error", "_last_call_ok", "_llm_fallbacks",
    "sub_task_id", "task_id", "branch", "commit_start", "commit_hash",
)

_AGENT_ID_RE = re.compile(r"agent_home/(\d+)")


def _agent_id_from_home(home: str) -> str:
    m = _AGENT_ID_RE.search(home or "")
    return m.group(1) if m else ""


def exec(inputs: dict, home: str) -> dict:
    try:
        from modules.sql.agents_repo import AgentsDB
        agent_id = str(inputs.get("agent_id") or _agent_id_from_home(home))
        if not agent_id:
            return {"ok": False, "error": "agent_id requis (ou home agent_home/<id>)"}
        keys = list(inputs.get("keys") or []) or list(_DEFAULT_KEYS)
        db = AgentsDB()
        row = db.conn.execute(
            "SELECT variables_json FROM agents WHERE agent_id = ?",
            (int(agent_id),)).fetchone()
        if not row:
            db.close()
            return {"ok": False, "error": f"agent {agent_id} introuvable"}
        try:
            v = json.loads(row["variables_json"] or "{}")
        except (json.JSONDecodeError, TypeError):
            v = {}
        cleared = []
        for k in keys:
            if k in v:
                v.pop(k, None)
                cleared.append(k)
        if cleared:
            db.conn.execute(
                "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
                (json.dumps(v), int(agent_id)))
            db.conn.commit()
        db.close()
        return {"ok": True, "cleared": cleared}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


__skills__ = ["exec"]
