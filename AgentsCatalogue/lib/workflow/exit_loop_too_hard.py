"""exit_loop_too_hard — bump de difficulté ou re-découpe (analysis).

Appelé par le LLM quand la tâche est trop difficile. Bump difficulty si le
niveau max de la team le permet (et que l'analyste le reprendra), sinon
crée un token analysis pour que l'analyste découpe le travail.
"""


def exec(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    reason = inputs.get("reason", "")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        from AgentsCatalogue.lib.workspacedb.task import _scope
        from modules.sqlite.workspace.workspace import _DIFFICULTY_RANK
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        # Plafond de difficulté : le niveau max de la team pour ce rôle.
        difficulty = task.get("difficulty") or "medium"
        cur_rank = _DIFFICULTY_RANK.get(difficulty, 1)
        # rôle de l'agent (coding → codeur ; on borne par la hiérarchie).
        task_type = task.get("task_type") or ""
        max_rank = 3  # expert
        if task_type == "coding":
            # un coding est traité par un codeur ; plafond expert si la team
            # a des seniors, medium sinon — on reste prudent : expert par défaut.
            max_rank = 3
        if cur_rank >= max_rank:
            # Déjà au plafond → re-découpe (analysis-split).
            from AgentsCatalogue.lib.workspacedb.task import create_token
            a = create_token({
                "workspace_id": workspace_id,
                "task": {
                    "title": f"re-découper: {task.get('title', '')}",
                    "description": (f"La tâche {task_id} est trop difficile "
                                    f"(difficulté {difficulty}, plafond atteint). "
                                    f"Raison : {reason}. Découpe-la en "
                                    f"sous-tokens plus petits."),
                    "task_type": "analysis",
                    "difficulty": "medium",
                },
                "parents": [{"task_id": int(task_id), "required_state": "done"}],
            }, home)
            db.close()
            return {"ok": True, "bumped": False, "new_difficulty": difficulty,
                    "ask_analysis": True,
                    "analysis_task_id": a.get("task", {}).get("task_id"),
                    "reason": reason}
        next_diff = [k for k, r in sorted(_DIFFICULTY_RANK.items(),
                                          key=lambda x: x[1])
                     if r > cur_rank]
        new_diff = next_diff[0] if next_diff else difficulty
        scope.tasks.update(int(task_id), difficulty=new_diff)
        db.close()
        return {"ok": True, "bumped": True, "new_difficulty": new_diff,
                "ask_analysis": False, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["exec"]
