"""Task management — create, list, claim, complete, lifecycle (clear/cancel)."""

from modules.sql.workspace import WorkspaceDB


def _scope(workspace_id: str):
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


def _git_quiet(clone: str, args) -> str:
    """Exécute git dans le clone, retourne la sortie stdout (best-effort)."""
    import subprocess
    try:
        r = subprocess.run(["git", "-C", clone] + list(args),
                           capture_output=True, text=True, timeout=15)
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def create(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    title = inputs.get("title", "")
    description = inputs.get("description", "")
    priority = int(inputs.get("priority", 0))
    difficulty = inputs.get("difficulty", "medium")
    task_type = inputs.get("task_type", inputs.get("role_required", ""))
    team_id = int(inputs.get("team_id", -1))
    repo = inputs.get("repo", "")
    branch = inputs.get("branch", "")
    base_commit = inputs.get("base_commit", "")
    commit_start = inputs.get("commit_start", "")
    branch_start = inputs.get("branch_start", "")
    primordial = 1 if inputs.get("primordial") else 0
    deadline = inputs.get("deadline", "")
    estimated_minutes = int(inputs.get("estimated_minutes", 0) or 0)
    # created_at hérité (split) : l'anti-famine part de la primordiale.
    created_at_iso = inputs.get("created_at", "") or None
    parents = inputs.get("parents", [])  # [{task_id, required_state}]
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id et title requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.create(title, description, priority,
                                  difficulty=difficulty, task_type=task_type,
                                  team_id=team_id, repo=repo, branch=branch,
                                  base_commit=base_commit,
                                  commit_start=commit_start,
                                  branch_start=branch_start,
                                  primordial=primordial,
                                  deadline=deadline,
                                  estimated_minutes=estimated_minutes,
                                  created_at_iso=created_at_iso)
        for dep in parents or []:
            pid = dep.get("task_id") if isinstance(dep, dict) else dep
            rs = dep.get("required_state", "done") if isinstance(dep, dict) else "done"
            if pid:
                scope.tasks.add_dependency(task["task_id"], int(pid), rs)
        db.close()
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create_token(inputs: dict, home: str) -> dict:
    """token_task_create — seule façon de CRÉER un token de tâche.

    inputs :
      - task : {title, description, task_type, difficulty, priority, repo,
                branch, base_commit, commit_start, branch_start, primordial,
                deadline, estimated_minutes, created_at}
      - parents : [{task_id, required_state}] (l'enfant n'est piochable que
        si tous ses parents sont à l'état requis)
      - workspace_id, team_id
    Retourne le token créé {status='todo'}.
    """
    workspace_id = inputs.get("workspace_id", "")
    task = inputs.get("task") or {}
    if not workspace_id or not task.get("title"):
        return {"ok": False, "error": "workspace_id + task.title requis"}
    return create({
        "workspace_id": workspace_id,
        "title": task.get("title"),
        "description": task.get("description", ""),
        "priority": task.get("priority", 0),
        "difficulty": task.get("difficulty", "medium"),
        "task_type": task.get("task_type", ""),
        "team_id": task.get("team_id", inputs.get("team_id", -1)),
        "repo": task.get("repo", ""),
        "branch": task.get("branch", ""),
        "base_commit": task.get("base_commit", ""),
        "commit_start": task.get("commit_start", ""),
        "branch_start": task.get("branch_start", ""),
        "primordial": task.get("primordial", inputs.get("primordial", 0)),
        "deadline": task.get("deadline", ""),
        "estimated_minutes": task.get("estimated_minutes", 0),
        "created_at": task.get("created_at", ""),
        "parents": inputs.get("parents", []),
    }, home)


def pick_token(inputs: dict, home: str) -> dict:
    """token_task_pick — seule façon d'OBTENIR un token de tâche.

    inputs :
      - task_types : [{type, max_difficulty}] — les types que l'agent sait
        traiter, avec le niveau de difficulté maximal piochable par type
        (le niveau de l'agent borne la difficulté). Ex. un coder senior :
        [{type: 'coding', max_difficulty: 'expert'}].
      - workspace_id, team_id (agent_id injecté → assigned_to)
    Pioche le token le plus prioritaire compatible, le passe en 'doing'.
    """
    workspace_id = inputs.get("workspace_id", "")
    team_id = int(inputs.get("team_id", -1))
    agent_id = str(inputs.get("agent_id", "") or "")
    accept_external = bool(inputs.get("accept_external_work", True))
    types_in = inputs.get("task_types") or []
    if isinstance(types_in, str):  # FSM : "[{type: coding, max_difficulty: expert}]"
        import re
        parsed = []
        for m in re.finditer(r"\{([^}]*)\}", types_in):
            body = m.group(1)
            t = re.search(r"type\s*:\s*[\"']?([\w]+)[\"']?", body)
            d = re.search(r"max_difficulty\s*:\s*[\"']?([\w]+)[\"']?", body)
            entry = {}
            if t:
                entry["type"] = t.group(1)
            if d:
                entry["max_difficulty"] = d.group(1)
            if entry:
                parsed.append(entry)
        types_in = parsed
    if isinstance(types_in, dict):  # tolérance {type: max_diff}
        types_in = [{"type": k, "max_difficulty": v} for k, v in types_in.items()]
    if not workspace_id or not types_in:
        return {"ok": False, "error": "workspace_id + task_types requis"}
    task_types = []
    max_diff = {}
    for t in types_in:
        tt = t.get("type", "") if isinstance(t, dict) else t
        mx = t.get("max_difficulty", "") if isinstance(t, dict) else ""
        if tt:
            task_types.append(tt)
            if mx:
                max_diff[tt] = mx
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.claim_next(task_types, max_diff, team_id=team_id,
                                      assigned_to=agent_id,
                                      accept_external_work=accept_external)
        if task:
            # 1er picker : déduire commit_start/branch_start depuis le clone si
            # la tâche ne les avait pas (elle pointe un repo/branche cibles).
            if not task.get("commit_start") or not task.get("branch_start"):
                _repo = task.get("repo") or ""
                _clone = str(home) + "/workspace/" + str(_repo) if _repo else ""
                _cs, _bs = task.get("commit_start"), task.get("branch_start")
                if _clone:
                    import os
                    if os.path.isdir(_clone + "/.git"):
                        _cs = _cs or _git_quiet(_clone, ["rev-parse", "HEAD"])
                        _bs = _bs or _git_quiet(_clone, ["branch", "--show-current"])
                if _cs != task.get("commit_start") or _bs != task.get("branch_start"):
                    scope.tasks.modify(task["task_id"],
                                       commit_start=_cs or task.get("commit_start"),
                                       branch_start=_bs or task.get("branch_start"))
                    task = scope.tasks.get(task["task_id"])
        db.close()
        if not task:
            return {"ok": False,
                    "error": "aucun token piochable pour les types demandés"}
        return {"ok": True, "task": task, "task_id": task["task_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def modify_token(inputs: dict, home: str) -> dict:
    """token_task_modify — transition d'un token vers l'étape suivante.

    Conceptuellement DÉTRUIT le token courant et CRÉE le suivant (même
    task_id) : change task_type (étape du pipeline) + status + libère le token.

    inputs :
      - task_id, workspace_id
      - new_task_type : étape suivante (ex. coding → code_review)
      - status : 'todo' (nouveau token à piocher) | 'doing' | 'done' | 'merged'
      - branch / commit_hash : livrable de l'étape
      - parents : [{task_id, required_state}] (ajoutés si fournis, ex. split)
      - clear_assigned : libérer le token (défaut True en transition)
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    new_task_type = inputs.get("new_task_type")
    status = inputs.get("status")
    clear = inputs.get("clear_assigned", True)
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.modify(
            int(task_id), new_task_type=new_task_type, status=status,
            branch=inputs.get("branch", ""),
            commit_hash=inputs.get("commit_hash", ""),
            clear_assigned=bool(clear))
        # Parents additionnels (split) : l'enfant dépend de ces parents.
        for dep in inputs.get("parents", []) or []:
            pid = dep.get("task_id") if isinstance(dep, dict) else dep
            rs = dep.get("required_state", "done") if isinstance(dep, dict) else "done"
            if pid:
                scope.tasks.add_dependency(int(task_id), int(pid), rs)
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _group_of(scope, task_id: int, include_self_primordial: bool = True):
    """Groupe de la tâche pour clear/cancel = la tâche + ses ANCÊTRES
    secondaires (les travaux splittés B,C dont un merge_split dépend).
    Les primordiales (racines) ne sont jamais incluses sauf si
    include_self_primordial et que la tâche ciblée est elle-même la racine."""
    task = scope.tasks.get(task_id)
    group = []
    # Ancêtres (travaux splittés) — remontée récursive, racine d'abord.
    for anc in scope.tasks.get_ancestors(task_id):
        row = scope.tasks.get(anc)
        if row and not row.get("primordial"):
            group.append(anc)
    # La tâche elle-même : incluse si secondaire, ou si c'est la racine ciblée.
    if task and (not task.get("primordial") or include_self_primordial):
        if task_id not in group:
            group.append(task_id)
    return group


def clear_task(inputs: dict, home: str) -> dict:
    """clear_task — supprime une tâche + son groupe (filiation).

    GROUPE = la tâche + ses ANCÊTRES secondaires (les travaux splittés dont
    un merge_split dépend). GARDE-FOUS :
      1. Chaque membre du groupe doit être `done` (ou `cancelled`) ET tous
         ses parents à l'état requis.
      2. Ne pas clear si un membre est parent (ancêtre) d'une autre tâche
         PRIMORDIALE (on ne détruit pas le travail d'autres groupes).
      3. Les primordiales racines ne sont supprimées que si c'est la tâche
         ciblée (le chat qui clôt sa mission).
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        group = _group_of(scope, int(task_id))
        # 1) Chaque membre done (ou cancelled) + parents à l'état requis.
        for tid in group:
            row = scope.tasks.get(tid)
            if not row:
                continue
            if row.get("status") != "done" and not row.get("cancelled"):
                db.close()
                return {"ok": False,
                        "error": f"tâche {tid} non terminée (status="
                                 f"{row.get('status')}) — clear refusé"}
            for p in scope.tasks.get_parents(tid):
                if p.get("status") != p.get("required_state"):
                    db.close()
                    return {"ok": False,
                            "error": f"parent de {tid} non à l'état requis — "
                                     "clear refusé"}
        # 2) Ne pas supprimer un membre qui est parent d'une AUTRE primordiale
        #    hors du groupe.
        group_set = set(group)
        for tid in group:
            for child in scope.tasks.get_children(tid):
                cid = child["task_id"]
                if cid not in group_set and child.get("primordial"):
                    db.close()
                    return {"ok": False,
                            "error": f"tâche {tid} parent de la primordiale "
                                     f"{cid} — clear refusé"}
        # 3) Supprimer le groupe (dépendances, fichiers, tâches).
        ph = ",".join("?" for _ in group)
        for tid in group:
            db.conn.execute("DELETE FROM task_dependencies WHERE task_id = ? "
                            "OR parent_id = ?", (tid, tid))
            db.conn.execute("DELETE FROM task_files WHERE task_id = ?", (tid,))
        db.conn.execute(f"DELETE FROM tasks WHERE task_id IN ({ph})", group)
        db.conn.commit()
        db.close()
        return {"ok": True, "deleted": group, "count": len(group)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def cancel_task(inputs: dict, home: str) -> dict:
    """cancel_task — annule une tâche + son groupe (doux, ne supprime rien).

    GROUPE = la tâche + ses ANCÊTRES secondaires (travaux splittés). Les
    primordiales racines ne sont pas annulées (sauf la tâche ciblée si elle
    est secondaire). Protège :
      - branche `canceled_<task_id>` sur le repo central local (position
        courante) pour ne pas perdre le code,
      - reset du clone au commit_start,
      - flag `cancelled` (pas un statut) sur le groupe — on peut revenir.

    inputs : task_id, workspace_id, project_id (repo), agent_id (clone), reason
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    project_id = inputs.get("project_id", "")
    agent_id = str(inputs.get("agent_id", "") or "")
    reason = inputs.get("reason", "")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        import subprocess
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        group = _group_of(scope, int(task_id))
        if not group:
            db.close()
            return {"ok": False,
                    "error": "aucune tâche secondaire à annuler — ciblez une "
                             "tâche du groupe"}
        # Arrêter les agents travaillant sur ces tâches (kill best-effort).
        stopped = []
        for tid in group:
            t = scope.tasks.get(tid)
            a = (t.get("assigned_to") or "") if t else ""
            if a:
                try:
                    from services.api.afd_client import get_afd_client
                    get_afd_client().call(a, "signal", type="kill")
                    stopped.append(a)
                except Exception:
                    pass
        # Protéger le travail : branche canceled_<id> sur le repo central.
        _branch = f"canceled_{task_id}"
        git_ok = False
        if project_id:
            try:
                from AgentsCatalogue.lib.git_ops import _central_repo, Sandbox
                bare = _central_repo(project_id)
                _sb = Sandbox()
                o, e, rc = _sb.run(
                    ["git", "--git-dir", str(bare), "rev-parse", "HEAD"],
                    shell=False, timeout=30)
                if rc == 0:
                    _sb.run(["git", "--git-dir", str(bare), "branch", "-f",
                             _branch, o.strip()], shell=False, timeout=30)
                    git_ok = True
            except Exception:
                git_ok = False
        # Reset du clone au commit_start (retirer les changements du groupe).
        clone = f"{home}/workspace/{project_id}" if project_id else ""
        if clone and task.get("commit_start"):
            try:
                import os
                if os.path.isdir(clone + "/.git"):
                    subprocess.run(["git", "-C", clone, "reset", "-q", "--hard",
                                    task.get("commit_start")],
                                   capture_output=True, timeout=30)
            except Exception:
                pass
        # Flag cancelled sur le groupe (le code reste, on peut revenir).
        for tid in group:
            scope.tasks.modify(tid, status="todo",
                               clear_assigned=True, cancelled=1)
        db.close()
        return {"ok": True, "cancelled": group, "count": len(group),
                "branch": _branch if git_ok else None,
                "stopped_agents": stopped, "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_pending()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_all(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_all()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if task:
            files = scope.tasks.get_files(int(task_id))
            task["files"] = files
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def claim(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    agent_name = inputs.get("agent_name", "")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    if not agent_name:
        return {"ok": False, "error": "agent_name requis"}
    try:
        db, scope = _scope(workspace_id)
        claimed = scope.tasks.claim(int(task_id), agent_name)
        db.close()
        if not claimed:
            return {"ok": False, "error": "tâche déjà prise ou introuvable"}
        return {"ok": True, "task_id": task_id}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def done(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    branch = inputs.get("branch", "")
    commit_hash = inputs.get("commit_hash", "")
    approve = bool(inputs.get("approve", False))
    delivered = bool(inputs.get("delivered", False))
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if task is None:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        # Garde anti faux-positif : une tâche de CODAGE ne peut pas être
        # marquée done sans livrable. Le livrable = un commit git (le code
        # produit) OU un fichier écrit (delivered=true, ex. rapport d'audit).
        # Sans ça, les membres greedy marquent des tâches done à vide et la
        # mission « avance » sans livrable réel.
        role = (task.get("role_required") or "").lower()
        if role.startswith("coder") and not (branch or commit_hash or delivered):
            db.close()
            return {"ok": False,
                    "error": "tâche de codage : commit_hash/branch requis "
                             "(travail non livré — commit d'abord via git)"}
        # Flux de review : une tâche de codage livrée passe en `review` (pas
        # directement done) — le reviewer la valide ensuite en `done` avec
        # approve=true. Sans ça, « une tâche n'est done que si reviewée » est
        # violée (le coder se marque done lui-même sans validation).
        if role.startswith("coder") and not approve:
            task = scope.tasks.set_status(int(task_id), "review",
                                          branch=branch,
                                          commit_hash=commit_hash)
            db.close()
            return {"ok": True, "task": task, "review": True,
                    "note": "tâche livrée en review — le reviewer doit la "
                            "valider (task_done approve=true)"}
        task = scope.tasks.set_status(int(task_id), "done",
                                      branch=branch,
                                      commit_hash=commit_hash)
        db.close()
        if not task:
            return {"ok": False, "error": "tâche introuvable"}
        return {"ok": True, "task": task}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def done_no_code(inputs: dict, home: str) -> dict:
    """Marque une tâche done SANS livrable code — exception vérifiée.

    À n'utiliser QU'après vérification (git_diff vide, tâche ne nécessitant
    pas de code). Passe `delivered=True` pour contourner la garde anti
    faux-positif de `done()`, et enregistre la raison pour l'audit.
    """
    reason = (inputs.get("reason") or "").strip()
    if not reason:
        return {"ok": False,
                "error": "reason requis : explique pourquoi la tâche est "
                         "done sans changement de code (git_diff vide, "
                         "dépendance externe…)"}
    # `delivered=True` : la tâche est considérée livrée (vérification faite).
    inputs["delivered"] = True
    result = done(inputs, home)
    if result.get("ok"):
        result["no_code_change"] = True
        result["reason"] = reason
    return result


def claim_next(inputs: dict, home: str) -> dict:
    """Pioche la prochaine tâche dispo pour le rôle de l'agent (greedy).

    La tâche au statut cible la plus prioritaire correspondant à role_required
    passe en 'running'. Par défaut pioche les 'pending' ; le reviewer pioche
    automatiquement les tâches livrées en 'review' (une tâche n'est done que
    si reviewée) avant les pending.
    """
    workspace_id = inputs.get("workspace_id", "")
    role_required = inputs.get("role_required", "")
    team_id = int(inputs.get("team_id", -1))
    status = inputs.get("status", "")
    # agent_id injecté par le workflow → utilisé comme assigned_to pour éviter
    # que deux greedy piochent la MÊME tâche en parallèle (double exécution).
    agent_id = str(inputs.get("agent_id", "") or "")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = None
        # Le reviewer valide d'abord les tâches livrées en review.
        if not status and role_required and "review" in role_required.lower():
            task = scope.tasks.claim_next(role_required=role_required,
                                          team_id=team_id, status="review",
                                          assigned_to=agent_id)
        if task is None:
            task = scope.tasks.claim_next(
                role_required=role_required, team_id=team_id,
                status=status or "pending", assigned_to=agent_id)
        db.close()
        if not task:
            return {"ok": False, "error": "aucune tâche dispo pour ce rôle"}
        return {"ok": True, "task": task, "task_id": task["task_id"]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verdict(inputs: dict, home: str) -> dict:
    """VERDICT du LLM après une tentative : done | continue | error | to_difficult.

    - done          → task_done (branch/commit si fournis).
    - continue      → rien, le FSM relance la boucle.
    - error         → tâche reste en cours, signal pour l'équipe.
    - to_difficult  → bump difficulty (easy→medium→hard→expert) si le niveau
                      max de la team le permet ; sinon ask_analysis_and_split.
    """
    verdict = (inputs.get("verdict") or "").strip().lower()
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    reason = (inputs.get("reason") or "").strip()
    if verdict not in ("done", "continue", "error", "to_difficult"):
        return {"ok": False, "error": "verdict invalide (done|continue|error|to_difficult)"}
    if verdict == "continue":
        return {"ok": True, "verdict": verdict, "reason": reason}
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis pour ce verdict"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if verdict == "done":
            result = done(inputs, home)
            db.close()
            if not result.get("ok"):
                return result
            result["verdict"] = "done"
            result["reason"] = reason
            return result
        if verdict == "error":
            # Signal d'erreur pour l'équipe : la tâche reste en cours (pas
            # abandonnée, un membre plus compétent peut la reprendre).
            db.close()
            return {"ok": True, "verdict": "error", "reason": reason,
                    "note": "tâche laissée en cours — signal pour l'équipe"}
        # to_difficult : bump de difficulté, plafonné au niveau max du rôle.
        difficulty = (task.get("difficulty") or "medium") if task else "medium"
        role = (task.get("role_required") or inputs.get("role_required") or "")
        _DIFF = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}
        cur_rank = _DIFF.get(difficulty, 1)
        max_rank = 3
        # Plafond : le niveau max de la team pour ce rôle (senior > mid > junior).
        if role and "_" in role:
            _lvl = {"junior": 0, "mid": 1, "senior": 2}
            level = role.rsplit("_", 1)[-1]
            lvl_rank = _lvl.get(level)
            if lvl_rank is not None:
                # senior → max_rank expert (niveau le plus haut gérable)
                max_rank = min(3, 1 + lvl_rank)
        if cur_rank >= max_rank:
            db.close()
            return {"ok": True, "verdict": "to_difficult", "difficulty_bumped": False,
                    "ask_analysis": True,
                    "reason": reason,
                    "note": (f"difficulté déjà au plafond ({difficulty}, max {role}) "
                             "→ re-découpe demandée (ask_analysis_and_split)")}
        next_diff = [k for k, r in sorted(_DIFF.items(), key=lambda x: x[1])
                     if r > cur_rank]
        new_diff = next_diff[0] if next_diff else difficulty
        scope.tasks.update(int(task_id), difficulty=new_diff)
        db.close()
        return {"ok": True, "verdict": "to_difficult", "difficulty_bumped": True,
                "ask_analysis": False, "new_difficulty": new_diff,
                "reason": reason}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def add_file(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    path = inputs.get("path", "")
    role = inputs.get("role", "source")
    if not workspace_id or task_id is None or not path:
        return {"ok": False, "error": "workspace_id, task_id et path requis"}
    try:
        db, scope = _scope(workspace_id)
        scope.tasks.add_file(int(task_id), path, role)
        db.close()
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def get_files(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id et task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        files = scope.tasks.get_files(int(task_id))
        db.close()
        return {"ok": True, "files": files}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def list_tasks(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    show_all = inputs.get("all", False)
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, scope = _scope(workspace_id)
        tasks = scope.tasks.list_all() if show_all else scope.tasks.list_pending()
        db.close()
        return {"ok": True, "tasks": tasks, "count": len(tasks)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["create", "list_pending", "list_all", "list_tasks", "get",
              "claim", "claim_next", "done", "done_no_code", "add_file",
              "get_files", "verdict", "create_token", "pick_token",
              "modify_token", "clear_task", "cancel_task"]
