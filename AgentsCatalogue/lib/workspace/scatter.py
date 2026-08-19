"""workspace/scatter@v1 — Décompose une mission en tâches dans le workspace.

Deux modes :
  - Avec LLM : fournir `request`, le skill décompose via LLM
  - Sans LLM : fournir `tasks` (liste explicite)

Les tâches sont créées dans le workspace. Les workers
les récupèrent via task_list/task_claim.
"""

import json
from typing import Dict, List, Optional


def _decompose_with_llm(request: str, context: str = "",
                         provider_ref: str = "", model_ref: str = "") -> List[Dict]:
    from modules.llm_manager.llm_manager import LLMManager
    from modules.llm_manager.resilient import resilient_chat
    bridge = LLMManager(cat=None).get_bridge()

    system = "Tu décomposes une mission en tâches. Réponds UNIQUEMENT un JSON list."
    if context:
        system += f"\nContexte : {context}"

    prompt = f"Décompose cette requête en 3-8 tâches :\n\n{request}\n\n[{{\"title\": \"...\", \"description\": \"...\", \"priority\": N}}]"

    p_ref = provider_ref or ""
    m_ref = model_ref or ""
    try:
        response = resilient_chat(p_ref, m_ref, [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ], timeout=30, fallback=True)
    except Exception:
        try:
            response = bridge.chat(p_ref, m_ref, messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ])
        except Exception:
            return []

    content = getattr(response, "content", "") or "[]"
    try:
        tasks = json.loads(content.strip().strip("```json").strip("```"))
        if isinstance(tasks, list):
            return tasks
    except (json.JSONDecodeError, AttributeError):
        pass
    return []


def exec(inputs: dict, home: str) -> dict:
    """Point d'entrée du skill workspace/scatter@v1.

    inputs:
        workspace_id: str (requis)
        request: str — mission à décomposer (si pas de tasks explicites)
        tasks: list[dict] — tâches explicites (optionnel)
        context: str — contexte additionnel pour le LLM
    """
    ws_id = inputs.get("workspace_id", "")
    if not ws_id:
        return {"ok": False, "error": "workspace_id required", "task_ids": []}

    request = inputs.get("request", "")
    explicit_tasks = inputs.get("tasks", [])
    context = inputs.get("context", "")
    worker_names = inputs.get("worker_names", [])
    provider_ref = inputs.get("provider_ref", "")
    model_ref = inputs.get("model_ref", "")

    if explicit_tasks:
        tasks = explicit_tasks
    elif request:
        tasks = _decompose_with_llm(request, context, provider_ref, model_ref)
        if not tasks:
            return {"ok": False, "error": "impossible de décomposer la requête", "task_ids": []}
    else:
        return {"ok": False, "error": "request ou tasks requis", "task_ids": []}

    # Créer les tâches dans le workspace
    from pathlib import Path
    from modules.sqlite.workspace.workspace import WorkspaceDB, TaskRepository

    ws_db_path = Path(home) / "workspaces" / ws_id / "workspace.db"
    if not ws_db_path.exists():
        return {"ok": False, "error": f"workspace DB not found: {ws_db_path}", "task_ids": []}

    wdb = WorkspaceDB(str(ws_db_path))
    tr = TaskRepository(wdb.conn, ws_id)

    task_ids = []
    for t in tasks:
        task = tr.create(
            title=t.get("title", "Tâche"),
            description=t.get("description", ""),
            priority=t.get("priority", 0),
        )
        task_ids.append(task.get("task_id", 0))

    # Notifier les workers via chat
    try:
        from modules.sqlite.workspace.workspace import ChatroomRepository
        chat = ChatroomRepository(wdb.conn, ws_id)
        chat.post(sender_agent_id=0, content=f"📋 {len(task_ids)} nouvelles tâches créées", msg_type="scatter")
    except Exception:
        pass

    wdb.close()

    # Réveiller les workers
    if worker_names:
        from services.agent_manager.service import AgentManager
        from modules.sql.db import AgentsDB
        _db = AgentsDB()
        _mgr = AgentManager(db=_db)
        for wname in worker_names:
            row = _db.conn.execute(
                "SELECT agent_id FROM agents WHERE name = ?", (wname,)
            ).fetchone()
            if row:
                _mgr.send_signal(row["agent_id"], "wakeup", {"workspace_id": ws_id, "task_count": len(task_ids)})
        _db.close()

    return {
        "ok": True,
        "task_ids": task_ids,
        "count": len(task_ids),
        "workspace_id": ws_id,
    }


__skills__ = ["exec"]