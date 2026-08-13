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


_ROLE_TO_TYPE = {
    "coder": "coding",
    "coder_senior": "coding",
    "coder_junior": "coding",
    "codeur": "coding",
    "coding": "coding",
    "tester": "testing_code",
    "test_runner": "testing_code",
    "testeur": "testing_code",
    "testing_code": "testing_code",
    "reviewer": "code_review",
    "relecteur": "code_review",
    "code_review": "code_review",
    "merger": "merge",
    "orchestrateur": "merge",
    "merge": "merge",
    "analyst": "analysis",
    "architecte": "analysis",
    "analysis": "analysis",
}


def _normalize_task_type(task_type: str) -> str:
    """Normalise un type/role de tâche vers un type de pipeline connu.

    Le pilote LLM (chat-pilot) crée parfois des tâches avec des RÔLES
    (coder_senior, tester, reviewer) au lieu des types de pipeline (coding,
    testing_code, code_review). Les greedy piochant par type (task_types
    [{type: coding}]), une tâche 'coder_senior' n'est jamais piochée → le
    swarm stagne. Le mapping ci-dessous aligne rôle → type.
    """
    key = str(task_type or "").strip().lower()
    if not key:
        return str(task_type or "")
    return _ROLE_TO_TYPE.get(key, str(task_type or ""))


def _normalize_repo_ref(repo: str) -> str:
    """Normalise la référence d'un repo pour git_clone.

    Le pilote/analyste met parfois un CHEMIN ABSOLU (ex.
    /home/…/.modelweaver/repos/sessions/swarm-xxx) au lieu de l'identifiant
    relatif que git_clone attend (sessions/swarm-xxx). On convertit tout
    chemin absolu sous .../repos/ en identifiant relatif."""
    r = str(repo or "").strip()
    if not r:
        return ""
    import re as _re
    # .../repos/sessions/swarm-xxx ou .../repos/sessions/swarm-xxx.git
    m = _re.search(r"(?:repos/)(sessions/[^/\s]+?)(?:\.git)?$", r)
    if m:
        return m.group(1)
    return r


# DEPRECATED (taskflow V0.15) — remplacé par workspacedb.taskflow.decoupe / sub_task_done
def create_sous_tache(inputs: dict, home: str) -> dict:
    """create_sous_tache — découpage par l'ANALYSTE en UN batch du MÊME type.

    RÈGLE STRICTE : toutes les sous-tâches partagent le `type` fourni (le tool
    rejette les types mixtes). L'analyste émet un appel PAR type s'il construit
    un pipeline. Chaque sous-tâche : {title, description, difficulty,
    task_to_do_before} (dépendances : task_id du workspace OU index 0-based du
    batch). Un `rapport_analysis` est attaché à la tâche parente (task_reports).
    """
    workspace_id = inputs.get("workspace_id", "")
    stype = _normalize_task_type(inputs.get("type", ""))
    parent_id = inputs.get("parent_task_id")
    sous = inputs.get("sous_taches") or []
    if not workspace_id or not stype or parent_id is None:
        return {"ok": False, "error": "workspace_id + type + parent_task_id requis"}
    if not isinstance(sous, list) or not sous:
        return {"ok": False, "error": "sous_taches : liste non vide requise"}
    try:
        db, scope = _scope(workspace_id)
        parent = scope.tasks.get(int(parent_id))
        if not parent:
            db.close()
            return {"ok": False, "error": "tâche parente introuvable"}
        # Référence du repo/branche héritée de la parente.
        repo = _normalize_repo_ref(parent.get("repo", ""))
        branch = parent.get("branch", "")
        team_id = parent.get("team_id", -1)
        created_ids = []
        batch_ids = {}  # index 0-based → task_id
        for i, st in enumerate(sous):
            title = (st.get("title") or "").strip()
            if not title:
                db.close()
                return {"ok": False, "error": f"sous-tâche {i}: title requis"}
            diff = st.get("difficulty") or "medium"
            created = scope.tasks.create(
                title=title,
                description=st.get("description") or "",
                difficulty=diff, task_type=stype, team_id=team_id,
                repo=repo, branch=branch, primordial=0)
            batch_ids[i] = created["task_id"]
            created_ids.append(created["task_id"])
        # Dépendances : parent + task_to_do_before (task_id ou index batch).
        for i, st in enumerate(sous):
            tid = batch_ids[i]
            scope.tasks.add_dependency(tid, int(parent_id), "done")
            before = st.get("task_to_do_before") or []
            if isinstance(before, int):
                before = [before]
            for b in before:
                pid = batch_ids.get(int(b)) if isinstance(b, int) else b
                if pid and int(pid) != tid:
                    try:
                        scope.tasks.add_dependency(tid, int(pid), "done")
                    except Exception:
                        pass
        # Rapport d'analyse attaché à la tâche parente.
        rapport = (inputs.get("rapport_analysis") or "").strip()
        if rapport:
            try:
                scope.tasks.add_report(int(parent_id), "analysis", rapport)
            except Exception:
                pass
        db.close()
        return {"ok": True, "created": created_ids, "count": len(created_ids),
                "type": stype}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# DEPRECATED (taskflow V0.15) — remplacé par workspacedb.taskflow.decoupe / sub_task_done
def assign_difficulte(inputs: dict, home: str) -> dict:
    """assign_difficulte — fixe la difficulté d'une tâche (ou la fait avancer).

    Pour l'analyste quand la découpe n'est pas nécessaire : on garde la tâche
    telle quelle, on ajuste juste le niveau (easy/medium/hard/expert). `next_type`
    fait avancer la tâche dans le pipeline (ex. analysis → coding) sans
    sous-découpage — utile si le découpage serait trivial. Le `rapport_analysis`
    est attaché à la tâche."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    updates = {}
    diff = (inputs.get("difficulty") or "").strip().lower()
    if diff:
        if diff not in ("easy", "medium", "hard", "expert"):
            return {"ok": False, "error": "difficulty invalide (easy|medium|hard|expert)"}
        updates["difficulty"] = diff
    next_type = (inputs.get("next_type") or "").strip()
    if next_type:
        updates["task_type"] = _normalize_task_type(next_type)
    rapport = (inputs.get("rapport_analysis") or "").strip()
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        if updates:
            scope.tasks.update(int(task_id), **updates)
        if rapport:
            try:
                scope.tasks.add_report(int(task_id), "analysis", rapport)
            except Exception:
                pass
        db.close()
        return {"ok": True, "updated": list(updates.keys()),
                "difficulty": updates.get("difficulty", task.get("difficulty")),
                "task_type": updates.get("task_type", task.get("task_type"))}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def report_read_only(inputs: dict, home: str) -> dict:
    """report_read_only — signale une commande refusée (non read-only).

    Loggue la demande comme un rapport (rôle 'readonly_request') sur la tâche
    courante, pour que le gestionnaire read-only la voie. L'analyste continue
    sans la commande en attendant."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    command = (inputs.get("command") or "").strip()
    if not command:
        return {"ok": False, "error": "command requis"}
    context = (inputs.get("context") or "").strip()
    niveau = (inputs.get("niveau") or "commande").strip()
    content = (f"[read-only request] commande={command!r} niveau={niveau} "
               f"contexte={context}")
    try:
        if task_id is not None:
            db, scope = _scope(workspace_id or "mw-llm-code")
            try:
                scope.tasks.add_report(int(task_id), "readonly_request", content)
            finally:
                db.close()
        return {"ok": True, "note": content[:200],
                "instruction": "analyse sans cette commande ; le gestionnaire "
                               "évaluera la demande"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def create(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    title = inputs.get("title", "")
    description = inputs.get("description", "")
    priority = int(inputs.get("priority", 0))
    difficulty = inputs.get("difficulty", "medium")
    task_type = _normalize_task_type(
        inputs.get("task_type", inputs.get("role_required", "")))
    team_id = int(inputs.get("team_id", -1))
    repo = _normalize_repo_ref(inputs.get("repo", ""))
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


def _finalize_pick(scope, task: dict, home: str) -> dict:
    """Finalise un pick : déduit commit_start/branch_start si absents, et
    retourne la tâche. Utilisé par pick_token (pick neuf + reprise)."""
    try:
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
    except Exception:
        pass
    return {"ok": True, "task": task, "task_id": task["task_id"]}


def pick_token(inputs: dict, home: str) -> dict:
    """token_task_pick — seule façon d'OBTENIR un token de tâche.

    inputs :
      - task_types : [{type, max_difficulty}] — les types que l'agent sait
        traiter, avec le niveau de difficulté maximal piochable par type
        (le niveau de l'agent borne la difficulté). Ex. un coder senior :
        [{type: 'coding', max_difficulty: 'expert'}].
      - workspace_id, team_id (agent_id injecté → assigned_to).
    REPRISE d'abord : si l'agent a déjà une tâche 'doing' assignée (run coupé),
    la recharger avant de piocher une nouvelle. Sinon pioche le token le plus
    prioritaire compatible et le passe en 'doing'.
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
        # ── 0. NETTOYAGE des tâches assignées à l'agent ──
        # Boucle : tant qu'une tâche assignée à cet agent est DONE ou
        # CANCELLED, on la LIBÈRE (release → todo) et on recommence. Un run
        # précédent a pu terminer/annuler la tâche mais laisser l'assignation :
        # la relâcher garantit un état propre et récupère les tâches
        # mal attribuées. (Si elle est 'doing' active → reprise ci-dessous.)
        while True:
            stale = scope.tasks.assigned_to_agent(
                agent_id, task_types=task_types)
            if not stale:
                break
            _released_any = False
            for t in stale:
                st = (t.get("status") or "")
                if st in ("done", "cancelled") or t.get("cancelled"):
                    scope.tasks.release(t["task_id"], freedby=agent_id)
                    _released_any = True
            if not _released_any:
                break  # toutes les assignées sont 'doing' → on les reprend
        # ── 1. REPRISE : une tâche 'doing' active assignée (run coupé) ──
        try:
            resume = scope.tasks.claim_resume(
                task_types=task_types, assigned_to=agent_id)
            if resume:
                task = resume
                return _finalize_pick(scope, task, home)
        except Exception:
            pass   # le skill claim_resume n'existe pas / erreur → pick normal
        # ── 2. PIOCHE d'une nouvelle tâche ──
        task = scope.tasks.claim_next(task_types, max_diff, team_id=team_id,
                                      assigned_to=agent_id,
                                      accept_external_work=accept_external)
        if task:
            return _finalize_pick(scope, task, home)
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


def release_token(inputs: dict, home: str) -> dict:
    """token_task_release — libère un token après échec d'un agent.

    Repasse le token en 'todo' (dans la pool), vide l'assignation, pose
    `freedby` (le dernier agent qui a échoué → rotation) ET crée un token
    `erreur_agent` (avec le descriptif) qu'un analyste_error piochera plus
    tard pour comprendre l'échec (recodage agent, modification tâche,
    réparation bridge, quota épuisé…).

    inputs :
      - workspace_id, task_id
      - freedby : agent qui a échoué
      - error : descriptif de l'erreur (pour le token erreur_agent)
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    freedby = inputs.get("freedby", "") or ""
    error_desc = inputs.get("error", "") or ""
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        # 1) Libérer le token : todo, plus d'assignation, freedby posé.
        released = scope.tasks.release(int(task_id), freedby=freedby)
        # 2) Produire le token erreur_agent (pour l'analyste_error futur).
        err_task = scope.tasks.create(
            title=f"erreur_agent: {task.get('title', '')}",
            description=error_desc or "échec de l'agent (sans détail)",
            task_type="erreur_agent",
            difficulty="easy",
            team_id=task.get("team_id", -1),
            repo=task.get("repo", ""),
            primordial=0)
        # Le token erreur_agent référence le token échoué (lien de filiation).
        scope.tasks.add_dependency(err_task["task_id"], int(task_id), "done")
        db.close()
        return {"ok": True, "task": released,
                "released_task_id": int(task_id),
                "error_task_id": err_task["task_id"],
                "freedby": freedby}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _group_of(scope, task_id, include_self_primordial: bool = False):
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


def apply_verdict(inputs: dict, home: str) -> dict:
    """APPLIQUE le verdict de review de façon MÉCANIQUE (parse le texte).

    Certains modèles répondent le verdict en TEXTE (« Verdict : DONE ») au lieu
    d'appeler le tool task_verdict → le reviewer boucle. On analyse le texte
    (review_out / verdict_out) et on applique via verdict() :
      - done      si le texte indique conforme (done/DONE/ok/conforme/valide)
      - error     si le texte indique échec/erreur/bloqué
      - continue  sinon (rework, problèmes)
    Retourne {verdict, applied, ...} ; `applied` False si le texte ne permet
    pas de trancher (le FSM garde ask_verdict comme secours)."""
    import re
    text = str(inputs.get("review_text") or inputs.get("verdict_text") or "")
    low = text.lower()
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not text.strip():
        return {"ok": True, "verdict": "", "applied": False,
                "note": "texte vide — verdict non applicable"}
    # done : marqueurs positifs (priorité haute : éviter un « not done »).
    done_hit = bool(re.search(
        r"(verdict\s*[:=]?\s*done|done\b|conforme|conformité ok|"
        r"livrable ok|valide\b|accepté|validé|review[:\s]*ok|\bok\b)",
        low))
    error_hit = bool(re.search(
        r"(error\b|erreur|échec|échoué|bloqué|failed|bug\b|crash\b|"
        r"non conforme|invalide|rej[ée]t|ne compile)", low))
    # « not done / non conforme » → pas un done malgré la présence de "ok".
    neg = bool(re.search(r"(not\s+done|non\s+conforme|pas\s+ok|n'?est\s+pas\s+ok)", low))
    if done_hit and not neg and not error_hit:
        v_result = "done"
    elif error_hit and not done_hit:
        v_result = "error"
    elif error_hit and done_hit:
        v_result = "error"   # ambigu mais signale un problème → pas done
    else:
        return {"ok": True, "verdict": "", "applied": False,
                "note": "verdict non détecté dans le texte"}
    if v_result in ("done", "error") and workspace_id and task_id is not None:
        r = verdict({"workspace_id": workspace_id, "task_id": task_id,
                     "verdict": v_result,
                     "reason": (inputs.get("reason") or "").strip() or
                               f"verdict extrait du texte du reviewer: {text[:200]}"},
                    home)
        r["verdict"] = v_result
        r["applied"] = bool(r.get("ok"))
        return r
    return {"ok": True, "verdict": v_result, "applied": False,
            "note": "verdict détecté mais workspace/task_id manquants"}


def review_verdict(inputs: dict, home: str) -> dict:
    """review_verdict — verdict de relecture : approve | disapprove | ask_more_intel.

    Le reviewer ne dispose QUE de ce tool pour conclure (pas d'exploration) :
      - approve(detail)      → la tâche passe `done` (livrable conforme). Le
                               détail (optionnel) est conservé en traçabilité.
      - disapprove(detail)   → le livrable ne convient pas : le DÉTAIL (les
                               corrections à apporter) est transmis au coder
                               sous forme d'une tâche corrective (coding)
                               rattachée à la tâche, qui reste en code_review.
      - ask_more_intel(list) → le contexte ne suffit pas : retourne la liste
                               exacte des fichiers/infos à fournir (le FSM les
                               lit et relance une review bornée).
    """
    decision = (inputs.get("decision") or "").strip().lower()
    detail = (inputs.get("detail") or "").strip()
    intel_needed = list(inputs.get("intel_needed") or [])
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if decision not in ("approve", "disapprove", "ask_more_intel"):
        return {"ok": False, "error": "decision invalide (approve|disapprove|ask_more_intel)"}
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    if decision == "ask_more_intel":
        return {"ok": True, "decision": decision, "applied": False,
                "intel_needed": intel_needed,
                "note": "le reviewer demande plus de contexte"}
    try:
        db, scope = _scope(workspace_id)
        task = scope.tasks.get(int(task_id))
        if decision == "approve":
            # La tâche passe done (via task_done).
            result = done({"workspace_id": workspace_id, "task_id": int(task_id),
                           "branch": (task or {}).get("branch", ""),
                           "commit_hash": (task or {}).get("commit_hash", "")},
                          home)
            db.close()
            result["decision"] = "approve"
            result["applied"] = bool(result.get("ok"))
            result["detail"] = detail
            return result
        # disapprove : transmettre le détail au coder via une tâche corrective.
        # La tâche reste en code_review (le retour du coder la re-mettra).
        corrections = detail or "livrable non conforme (le reviewer n'a pas détaillé)"
        corrective = scope.tasks.create(
            title=f"correction: {task.get('title', '')}" if task else "correction",
            description=corrections,
            difficulty="easy",
            task_type="coding",
            team_id=(task or {}).get("team_id", -1),
            repo=(task or {}).get("repo", ""),
            primordial=0,
            priority=100)
        # La tâche corrective dépend de la tâche revue (faite → correctif).
        try:
            scope.tasks.add_dependency(corrective["task_id"], int(task_id), "done")
        except Exception:
            pass
        db.close()
        return {"ok": True, "decision": "disapprove", "applied": False,
                "detail": corrections,
                "corrective_task_id": corrective["task_id"],
                "note": "correction demandée — tâche corrective créée"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def task_has_corrective(inputs: dict, home: str) -> dict:
    """Vérifie si une tâche corrective (coding) dépend de la tâche reviewée.

    `review_verdict(disapprove)` crée une corrective → présente → le reviewer a
    conclu (disapprove). Si aucune corrective : soit approve (done), soit
    ask_more_intel (à re-reviewer). Retourne {has_corrective, corrective_id}."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        db, scope = _scope(workspace_id)
        descendants = scope.tasks.get_descendants(int(task_id))
        corrective_id = None
        for did in descendants:
            t = scope.tasks.get(did)
            if t and t.get("task_type") == "coding":
                corrective_id = did
                break
        db.close()
        return {"ok": True, "has_corrective": corrective_id is not None,
                "corrective_task_id": corrective_id}
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
              "get_files", "verdict", "apply_verdict", "review_verdict",
              "task_has_corrective", "create_token", "pick_token",
              "modify_token", "clear_task", "cancel_task", "release_token",
              "report_read_only"]
