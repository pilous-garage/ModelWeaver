"""Taskflow (V0.15) — skills du nouveau système task/sub_task.

Fonctions exposées comme skills :
  - decoupe          (analysis/decoupe@v1)  : découpe A → B,C + merge auto
  - ask_new_task     (task_ask_new)         : cycle greedy (attribution synchrone)
  - sub_task_done    : marque une sub_task done + tag
  - sub_task_release : libère une sub_task (échec → cancelled/unattributed)
  - sub_task_get     : lit une sub_task
  - sub_task_list    : sub_tasks d'un agent ou d'une tâche

Le workflow est porté par la CRÉATION de nouvelles sub_tasks + dépendances
(état + tag requis), pas par des mutations de type.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sql.workspace import WorkspaceDB

# Types de sub_task connus (noyau) + tags par type (posés par l'agent).
TASK_TAGS: Dict[str, List[str]] = {
    "analysis": ["ok", "failure", "need_split"],
    "coding": ["done/ok", "done/failure", "done/need_split"],
    "testing": ["ok", "fail", "no_test"],
    "review": ["ok", "fail"],
    "merge": ["ok", "conflict"],
    "respond": ["ok"],
    "exploration": ["ok", "fail"],
    "avis": ["ok", "fail"],  # demande d'avis → le consensus la pick
}


def _scope(workspace_id: str):
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


def _find_subtask_workspace(db, sub_task_id: int) -> Optional[str]:
    """Retrouve le workspace d'une sub_task (les sub_tasks vivent dans la BDD
    workspace globale, une table `sub_tasks` par workspace ? non — la table est
    commune, workspace_id est une colonne)."""
    try:
        row = db.conn.execute(
            "SELECT workspace_id FROM sub_tasks WHERE sub_task_id = ?",
            (sub_task_id,)).fetchone()
        if row:
            return row["workspace_id"]
    except Exception:
        pass
    return None


def _current_analysis(db, sc, task_id: int):
    """La sub_task analysis courante de la tâche (doing d'abord, sinon la plus
    récente unattributed)."""
    rows = sc.sub_tasks.list_for_task(int(task_id))
    for r in rows:
        if r["sub_task_type"] == "analysis" and r["status"] == "doing":
            return r
    for r in rows:
        if r["sub_task_type"] == "analysis" and r["status"] in (
                "unattributed", "waiting_dependencies"):
            return r
    return None


# ── decoupe : analysis/decoupe@v1 ─────────────────────────────────────────

def decoupe(inputs: dict, home: str) -> dict:
    """Découpe en UNE passe (skill analysis/decoupe@v1) — tool UNIQUE.

    L'analyste ne connaît PAS les vrais task_id : il référence sa tâche
    courante par l'INDEX 0 et crée des tâches 1…n (par type de tâche), avec
    `task_content` et des `dependencies` RELATIVES (index).

    format `types` :
      [{type: "coding", tasks: [{task_id: 1, task_content: "...", dependencies: []},
                                {task_id: 2, ...}]},
       {type: "testing", tasks: [{task_id: 3, task_content: "...", dependencies: [1,2]}]}]

    Le skill RÉSOUT les index et génère les merges :
      - dépendance unique → lien direct ;
      - dépendances multiples (C dépend de A,B) → merge intermédiaire
        merge(A,B) → C (start C après fusion de A,B) ;
      - merge FINAL merge(A,B,C,D) → la sub_task analysis (index 0) attend ce
        merge final (start x = re-analysis post-merge).
    Le merge est intelligent (git gère les commits déjà mergés).

    Cas SIMPLE (`types` vide) : rien à découper → assigne `difficulty` et clôt
    l'analyse (done/ok). `analyse` (rapport) est toujours enregistré.
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    sub_task_id = inputs.get("sub_task_id")
    types = inputs.get("types") or []
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        # ── Correction d'identité : le modèle met souvent workspace_id inventé
        # (ex. "default", "todo_cli") et task_id = 0 (index relatif). La sub_task
        # courante (sub_task_id) porte la VRAIE tâche/workspace → on résout
        # depuis elle quand le task_id/workspace du modèle est invalide.
        cur = None
        _resolved_ws = workspace_id
        _resolved_tid = task_id
        if sub_task_id is not None:
            cur = sc.sub_tasks.get(int(sub_task_id))
            if not cur:
                # workspace du modèle invalide : cherche la sub_task dans les
                # autres workspaces (la BDD workspace est globale).
                _alt = _find_subtask_workspace(db, int(sub_task_id))
                if _alt:
                    db.close()
                    db, sc = _scope(_alt)
                    _resolved_ws = _alt
                    cur = sc.sub_tasks.get(int(sub_task_id))
            if cur:
                _tid_real = cur.get("task_id")
                if _tid_real is not None:
                    _resolved_tid = _tid_real
        workspace_id = _resolved_ws
        task_id = _resolved_tid
        task = sc.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        team_id = task.get("team_id", -1)
        repo = task.get("repo", "") or ""
        branch = task.get("branch", "") or ""
        if not cur:
            cur = _current_analysis(db, sc, task_id)

        # ── Cas SIMPLE : rien à découper → assigner difficulté + clore ──
        if not types:
            diff = (inputs.get("difficulty") or "").strip().lower()
            diff = {"facile": "easy", "moyen": "medium", "moyenne": "medium",
                    "difficile": "hard", "simple": "easy"}.get(diff, diff)
            if diff not in ("easy", "medium", "hard", "expert"):
                diff = task.get("difficulty") or "medium"
            sc.tasks.update(int(task_id), difficulty=diff)
            if cur:
                sc.sub_tasks.update(cur["sub_task_id"], difficulty=diff)
                sc.sub_tasks.set_status(cur["sub_task_id"], "done", tag="ok")
            _save_analyse(sc, task_id, inputs)
            # Tâche d'entrée (swarm-as-llm) : cas simple.
            #   - code (feature) : on crée une sub_task `coding` pour que le
            #     codeur l'implémente (le respond n'aurait rien à synthétiser).
            #   - simple (chat_entry) / texte (completion_entry) : PAS de code
            #     → la réponse est produite par le respond directement (le cas
            #     simple est clôturé ici, le respond synthétise la réponse).
            stype_task = (task.get("task_type") or "").lower()
            if stype_task == "feature":
                coding = sc.sub_tasks.create(
                    task_id=int(task_id), sub_task_type="coding",
                    difficulty=diff, status="unattributed",
                    team_id=team_id,
                    repo=task.get("repo", ""), branch=task.get("branch", ""),
                    description=(inputs.get("analyse") or
                                 task.get("description") or "")[:2000])
                db.close()
                return {"ok": True, "mode": "decoupe",
                        "created": [coding.get("sub_task_id")],
                        "analysis_sub_task_id": cur["sub_task_id"] if cur else None}
            db.close()
            return {"ok": True, "mode": "assign_difficulte",
                    "difficulty": diff,
                    "analysis_sub_task_id": cur["sub_task_id"] if cur else None}

        # ── Cas DÉCOUPE : créer les sub_tasks (index relatifs 1+) ──
        created: Dict[int, int] = {}  # index relatif → sub_task_id
        for t in types:
            stype = (t.get("type") or "").strip().lower()
            if not stype:
                continue
            for tsk in t.get("tasks") or []:
                idx = tsk.get("task_id")
                content = (tsk.get("task_content") or tsk.get("title") or "").strip()
                if idx is None or not content:
                    continue
                ns = sc.sub_tasks.create(
                    task_id=int(task_id), sub_task_type=stype,
                    difficulty=(tsk.get("difficulty") or tsk.get("niveau")
                                or "medium"),
                    status="unattributed",
                    repo=repo, branch=branch, team_id=team_id,
                    description=content)
                created[int(idx)] = ns["sub_task_id"]
        if not created:
            db.close()
            return {"ok": False, "error": "aucune sous-tâche à créer (types mal formés)"}

        # ── Dépendances : directes (1 dep) ou merge intermédiaire (n deps) ──
        for t in types:
            for tsk in t.get("tasks") or []:
                idx = tsk.get("task_id")
                if idx is None or int(idx) not in created:
                    continue
                deps = tsk.get("dependencies") or []
                tid = created[int(idx)]
                if len(deps) == 1 and deps[0] in created:
                    sc.sub_tasks.add_dependency(tid, created[int(deps[0])],
                                                "done", "")
                elif len(deps) > 1:
                    # merge intermédiaire : fusion des parents avant la tâche
                    m = sc.sub_tasks.create(
                        task_id=int(task_id), sub_task_type="merge",
                        difficulty="medium", status="waiting_dependencies",
                        repo=repo, branch=branch, team_id=team_id)
                    for d in deps:
                        if d in created:
                            sc.sub_tasks.add_dependency(m["sub_task_id"],
                                                        created[int(d)], "done", "")
                    sc.sub_tasks.add_dependency(tid, m["sub_task_id"], "done", "ok")

        # ── Merge FINAL : toutes les sub_tasks → x (analysis) attend ──
        merge_final = sc.sub_tasks.create(
            task_id=int(task_id), sub_task_type="merge",
            difficulty="medium", status="waiting_dependencies",
            repo=repo, branch=branch, team_id=team_id)
        for idx in created:
            sc.sub_tasks.add_dependency(merge_final["sub_task_id"],
                                        created[idx], "done", "")
        if cur:
            sc.sub_tasks.waiting_dependencies(cur["sub_task_id"])
            sc.sub_tasks.add_dependency(cur["sub_task_id"],
                                        merge_final["sub_task_id"], "done", "ok")

        _save_analyse(sc, task_id, inputs)
        db.close()
        return {"ok": True, "mode": "decoupe",
                "created": list(created.values()),
                "count": len(created),
                "merge_sub_task_id": merge_final["sub_task_id"],
                "analysis_sub_task_id": cur["sub_task_id"] if cur else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ask_intel(inputs: dict, home: str) -> dict:
    """ask_intel — demande d'info au sous-agent EXPLORER (tool de tout agent).

    L'agent précise les intels demandés (questions/éléments à regarder). Le
    skill crée une sub_task `exploration` (unattributed) avec la liste des
    intels ; la sub_task courante passe waiting_dependencies de l'exploration.
    Quand l'exploration est done (rapport produit), la sub_task redevient
    unattributed → l'agent la reprend avec les nouvelles infos.
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    sub_task_id = inputs.get("sub_task_id")
    intels = inputs.get("intels") or []
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    if isinstance(intels, str):
        import re
        intels = [s.strip() for s in re.split(r"[\n;]", intels) if s.strip()]
    try:
        db, sc = _scope(workspace_id)
        task = sc.tasks.get(int(task_id))
        team_id = task.get("team_id", -1) if task else -1

        cur = None
        if sub_task_id is not None:
            cur = sc.sub_tasks.get(int(sub_task_id))

        # ── Garde anti-boucle : l'analysis attend-elle déjà une exploration ? ──
        if cur:
            for dep in sc.sub_tasks.get_parents(cur["sub_task_id"]):
                p = sc.sub_tasks.get(dep["parent_id"])
                if p and p["sub_task_type"] == "exploration" \
                        and p["status"] not in ("done", "supervised", "cancelled"):
                    db.close()
                    return {"ok": True, "already_waiting": True,
                            "exploration_sub_task_id": p["sub_task_id"],
                            "note": "l'analyse attend déjà une exploration — "
                                    "demande ignorée (anti-boucle)"}
            # ── Plafond : le sub_task courant ne doit pas créer plus de 5
            # explorations (ask_intel en boucle = goulot). Au-delà, on refuse :
            # l'agent conclut avec le contenu disponible. ──
            MAX_EXPLORATIONS = 5
            try:
                n_expl = sc.conn.execute(
                    "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                    "AND sub_task_type = 'exploration' "
                    "AND supervised = 1", (int(task_id),)).fetchone()[0]
                if n_expl >= MAX_EXPLORATIONS:
                    db.close()
                    return {"ok": False, "too_many_explorations": True,
                            "note": f"déjà {n_expl} explorations supervisées — "
                                    "conclure avec le contenu disponible"}
            except Exception:
                pass

        # ── Redondance : mêmes intels déjà demandés ? ──
        norm = sorted(set(i.strip().lower() for i in intels if i.strip()))
        redundant = 0
        if norm:
            prev = sc.tasks.get_reports(int(task_id), roles=["exploration_request"])
            for r in prev or []:
                body = str(r.get("content") or "").lower()
                if all(i in body for i in norm):
                    redundant += 1

        desc = "INTELS DEMANDÉS :\n" + "\n".join(f"- {i}" for i in intels)
        exp = sc.sub_tasks.create(
            task_id=int(task_id), sub_task_type="exploration",
            difficulty="medium", status="unattributed",
            repo=task.get("repo", "") if task else "",
            branch=task.get("branch", "") if task else "",
            team_id=team_id, description=desc)
        # l'exploration doit fournir un rapport (tag ok)
        if cur:
            sc.sub_tasks.waiting_dependencies(cur["sub_task_id"])
            sc.sub_tasks.add_dependency(cur["sub_task_id"],
                                        exp["sub_task_id"], "done", "ok")
        # rapport d'exploration attendu : on stocke la demande dans le rapport
        sc.tasks.add_report(int(task_id), "exploration_request", desc)
        _save_analyse(sc, task_id, inputs)
        db.close()
        return {"ok": True, "exploration_sub_task_id": exp["sub_task_id"],
                "intels": intels, "redundant_count": redundant,
                "analysis_sub_task_id": cur["sub_task_id"] if cur else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _save_analyse(sc, task_id: int, inputs: dict) -> None:
    """Enregistre le rapport d'analyse (paramètre `analyse` ou rapport_analysis)."""
    rapport = (inputs.get("analyse") or inputs.get("rapport_analysis") or "").strip()
    if rapport:
        try:
            sc.tasks.add_report(int(task_id), "analysis", rapport)
        except Exception:
            pass


# ── ask_new_task : cycle greedy (remplace sleep + pick) ───────────────────

def ask_new_task(inputs: dict, home: str) -> dict:
    """Ask_new_task — l'agent greedy demande du travail au supervisor.

    1. sub_tasks déjà attribuées à l'agent (faux départ/relaunch) → la plus
       ancienne (priorité déjà triée par le supervisor à l'attribution).
    2. Sinon : écrit la demande en BDD (file de secours + traçabilité) puis
       appelle le supervisor EN SYNCHRONE : attribution directe (doing) ou
       réponse "en attente" (l'agent se déshydrate).

    inputs :
      - workspace_id, agent_id
      - types : [{type, level_max}] — types traitables + niveau max (un agent
        peut gérer plusieurs types). Niveau : easy|medium|hard|expert.
    """
    workspace_id = inputs.get("workspace_id", "")
    agent_id = inputs.get("agent_id", "")
    types = inputs.get("types") or []
    if isinstance(types, str):
        # FSM : "[{type: analysis, level_max: expert}]" (littéral yaml)
        import re
        parsed = []
        for m in re.finditer(r"\{([^}]*)\}", types):
            body = m.group(1)
            t = re.search(r"type\s*:\s*[\"']?([\w]+)[\"']?", body)
            d = re.search(r"level_max\s*:\s*[\"']?([\w]+)[\"']?", body)
            entry = {}
            if t:
                entry["type"] = t.group(1)
            if d:
                entry["level_max"] = d.group(1)
            if entry:
                parsed.append(entry)
        types = parsed
    if not agent_id:
        return {"ok": False, "error": "agent_id requis"}
    try:
        aid = int(str(agent_id).split("_")[-1])
    except (ValueError, TypeError):
        aid = 0
    try:
        # Résolution du workspace : si workspace_id absent (l'agent est réveillé
        # par le waker SANS contexte — variables vides), on retrouve la sub_task
        # doing/attributed assignée à l'agent dans TOUT le workspace (la BDD
        # sub_tasks est globale) et on résout son workspace_id.
        if not workspace_id:
            from modules.sql.workspace import WorkspaceDB as _WDB
            _wdb = _WDB()
            row = _wdb.conn.execute(
                "SELECT workspace_id FROM sub_tasks "
                "WHERE assigned_to = ? AND status IN ('doing','attributed') "
                "ORDER BY updated_at LIMIT 1",
                (f"agent:{aid}",)).fetchone()
            if row:
                workspace_id = row["workspace_id"]
            _wdb.close()
        db, sc = _scope(workspace_id)
        agent_name = f"agent:{aid}" if not str(aid).startswith("agent") else str(aid)
        # 1) PRIORITÉ REPRISE : sub_task `doing` déjà assignée à l'agent
        # (bug/crash → on reprend où on en était). On ne réinitialise PAS le
        # home (resumed=True → pas de reset_variable_after_change_task).
        already = sc.sub_tasks.list_assigned_to(agent_name)
        if already:
            st = sorted(already, key=lambda s: s["priority"], reverse=True)[0]
            payload = {"ok": True, "sub_task": dict(st),
                       "sub_task_id": st["sub_task_id"],
                       "task_id": st["task_id"], "type": st["sub_task_type"],
                       "resumed": "true",
                       "conv_id": _new_conv_id(st["task_id"])}
            _attach_task_ctx(sc, st["task_id"], payload)
            db.close()
            return payload
        # 2) PRIORITÉ PICK : sub_task `attributed` assignée à l'agent, la plus
        # haute priorité (le supervisor a choisi l'agent, on la prend en doing).
        mine = sc.sub_tasks.pick_for(agent_name)
        if mine:
            st = dict(mine)
            payload = {"ok": True, "sub_task": st,
                       "sub_task_id": st["sub_task_id"],
                       "task_id": st["task_id"], "type": st["sub_task_type"],
                       "resumed": "false",
                       "conv_id": _new_conv_id(st["task_id"])}
            _attach_task_ctx(sc, st["task_id"], payload)
            db.close()
            return payload
        # 3) Sinon : demande au supervisor (attribution unattributed → attributed).
        ask_id = sc.ask.create(aid, types)
        db.close()
        from services.task_supervisor.service import TaskSupervisor
        sup = TaskSupervisor()
        res = sup.assign(workspace_id, aid, types)
        if res.get("ok"):
            # l'assign retourne la sub_task `attributed` → l'agent la prend
            # immédiatement en doing (elle lui est assignée).
            sc2 = _scope(workspace_id)[1]
            sc2.sub_tasks.pick(res["sub_task_id"], agent_name)
            payload = {"ok": True, "sub_task": res["sub_task"],
                       "sub_task_id": res["sub_task_id"],
                       "task_id": res["task_id"], "type": res["type"],
                       "resumed": "false",
                       "ask_id": ask_id,
                       "conv_id": _new_conv_id(res["task_id"])}
            return _attach_task_ctx(sc2, res["task_id"], payload)
        # Rien de dispo → l'agent se déshydrate, mais on l'enregistre en
        # wait_for (sub_task_available) pour que le waker le réveille quand une
        # sub_task de son type devient dispo.
        _register_wait(workspace_id, aid, types)
        return {"ok": False, "reason": "wait", "ask_id": ask_id,
                "note": res.get("note", "aucune sub_task dispo")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _new_conv_id(task_id: int) -> str:
    """Nouvelle conversation par run greedy : tâche + timestamp."""
    import time as _t
    return f"{task_id}_{int(_t.time())}"


def _attach_task_ctx(sc, task_id, payload: dict) -> dict:
    """Enrichit le payload de pick avec le contexte git de la TÂCHE parente.

    repo/branch/commit_start permettent au greedy d'initialiser son workspace
    (clone du repo, checkout de la branche, snapshot du commit de départ) et de
    pousser ses livrables sur la bonne branche à la fin. workspace_id est aussi
    injecté : l'agent greedy doit connaître le workspace du run pour les steps
    suivants (ask_intel/sub_task_done reçoivent {{workspace_id}}).

    Variables pour le respond (réponse finale) :
      - task_message : le MESSAGE ORIGINAL (la question de l'utilisateur) —
        « Réponds au message : {{task_message}} ».
      - task_context : le CONTEXTE ORIGINAL (rapports/produits des autres
        agents) — fourni tel quel, « Contexte original : {{task_context}} ».
    Le respond répond au message en s'appuyant sur le contexte, SANS aller-
    chercher agentic (les outils classiques ne lui sont PAS donnés)."""
    try:
        task = sc.tasks.get(int(task_id))
        if task:
            payload["workspace_id"] = sc.wid or payload.get("workspace_id") or ""
            payload.setdefault("repo", task.get("repo") or "")
            payload.setdefault("branch", task.get("branch") or "")
            payload.setdefault("commit_start",
                               task.get("commit_start") or "")
            payload.setdefault("project_id", task.get("repo") or "mw-swarm")
            # Message original (la question de l'utilisateur).
            title = (task.get("title") or "").strip()
            desc = (task.get("description") or "").strip()
            msg = title
            if desc and desc != title:
                msg = f"{title}\n{desc}".strip()
            payload["task_message"] = msg
            # Contexte original : rapports/produits des autres agents.
            try:
                parts = []
                for r in (sc.tasks.get_reports(int(task_id)) or []):
                    role = r.get("role", "work")
                    body = str(r.get("content") or "").strip()
                    if body:
                        parts.append(f"### {role}\n{body}")
                payload["task_context"] = "\n\n".join(parts)
            except Exception:
                payload["task_context"] = ""
    except Exception:
        pass
    return payload


def _register_wait(workspace_id: str, agent_id: int, types: list) -> None:
    """Enregistre l'agent en attente d'une sub_task de son type (wait_for)."""
    try:
        from modules.sql.db import AgentsDB
        adb = AgentsDB()
        team_id = -1
        try:
            import json as _json
            row = adb.conn.execute(
                "SELECT variables_json FROM agents WHERE agent_id = ?",
                (agent_id,)).fetchone()
            if row:
                _v = _json.loads(row["variables_json"] or "{}")
                team_id = int(_v.get("team_id", -1) or -1)
        except Exception:
            pass
        adb.close()
        from AgentsCatalogue.lib.workspacedb.wait import registrer
        registrer({"agent_id": agent_id, "type": "sub_task_available",
                   "workspace_id": workspace_id, "team_id": team_id,
                   "types": types}, "")
    except Exception:
        pass


# ── sub_task lifecycle ────────────────────────────────────────────────────

def sub_task_done(inputs: dict, home: str) -> dict:
    """Marque une sub_task done + tag (posé par l'agent d'exécution).

    La garde livrable reste : une sub_task coding/testing/review/merge nécessite
    un livrable (branch/commit_hash) sauf mention explicite delivered=true
    (ex. no_test, done/need_split)."""
    workspace_id = inputs.get("workspace_id", "")
    sub_task_id = inputs.get("sub_task_id")
    tag = (inputs.get("tag") or "").strip()
    branch = inputs.get("branch", "") or ""
    commit_hash = inputs.get("commit_hash", "") or ""
    delivered = bool(inputs.get("delivered", False))
    rapport = (inputs.get("rapport") or "").strip()
    if not workspace_id or sub_task_id is None:
        return {"ok": False, "error": "workspace_id + sub_task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        st = sc.sub_tasks.get(int(sub_task_id))
        if not st:
            db.close()
            return {"ok": False, "error": "sub_task introuvable"}
        # Tags valides par type.
        allowed = TASK_TAGS.get(st["sub_task_type"], [])
        if allowed and tag and tag not in allowed:
            db.close()
            return {"ok": False,
                    "error": f"tag '{tag}' invalide pour {st['sub_task_type']} "
                             f"(attendu: {allowed})"}
        # Garde livrable.
        if st["sub_task_type"] in ("coding", "review", "merge") \
                and not (branch or commit_hash or delivered):
            db.close()
            return {"ok": False,
                    "error": f"sub_task {st['sub_task_type']}: branch/commit_hash "
                             "requis (livrable non fourni)"}
        sc.sub_tasks.set_status(int(sub_task_id), "done", tag=tag,
                                commit_hash=commit_hash)
        if branch:
            sc.sub_tasks.update(int(sub_task_id), branch=branch)
        # Rapport produit (ex. rapport d'exploration) → task_reports.
        if rapport:
            role = {"exploration": "exploration", "respond": "respond"}.get(
                st["sub_task_type"], "work")
            try:
                sc.tasks.add_report(st["task_id"], role, rapport)
            except Exception:
                pass
        st = sc.sub_tasks.get(int(sub_task_id))
        db.close()
        return {"ok": True, "sub_task": st}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sub_task_release(inputs: dict, home: str) -> dict:
    """Libère une sub_task (échec agent) : unattributed + freedby + tag.

    Le tag qualifie l'échec (ex. done/failure) → le supervisor décidera de la
    suite (re-découpe, fix, etc.) selon les règles de la team."""
    workspace_id = inputs.get("workspace_id", "")
    sub_task_id = inputs.get("sub_task_id")
    freedby = inputs.get("freedby", "") or ""
    tag = (inputs.get("tag") or "").strip()
    if not workspace_id or sub_task_id is None:
        return {"ok": False, "error": "workspace_id + sub_task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        st = sc.sub_tasks.release(int(sub_task_id), freedby=freedby, tag=tag)
        db.close()
        return {"ok": True, "sub_task": st}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sub_task_too_hard(inputs: dict, home: str) -> dict:
    """sub_task_too_hard — l'agent ABANDONNE (doing → too_hard).

    Trop difficile pour lui : passe la sub_task à `too_hard` avec la raison et
    incrémente too_hard_count. Le supervisor traitera les too_hard (bump de
    difficulté + re-attribution, ou re-découpe si limite de boucle atteinte).
    """
    workspace_id = inputs.get("workspace_id", "")
    sub_task_id = inputs.get("sub_task_id")
    reason = (inputs.get("reason") or "").strip()
    if not workspace_id or sub_task_id is None:
        return {"ok": False, "error": "workspace_id + sub_task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        st = sc.sub_tasks.mark_too_hard(int(sub_task_id), reason=reason)
        db.close()
        if not st:
            return {"ok": False, "error": "sub_task introuvable ou pas doing"}
        return {"ok": True, "sub_task": st,
                "too_hard_count": st.get("too_hard_count", 0)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sub_task_get(inputs: dict, home: str) -> dict:
    workspace_id = inputs.get("workspace_id", "")
    sub_task_id = inputs.get("sub_task_id")
    if not workspace_id or sub_task_id is None:
        return {"ok": False, "error": "workspace_id + sub_task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        st = sc.sub_tasks.get(int(sub_task_id))
        db.close()
        if not st:
            return {"ok": False, "error": "sub_task introuvable"}
        return {"ok": True, "sub_task": st}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def sub_task_list(inputs: dict, home: str) -> dict:
    """sub_task_list — sub_tasks d'une tâche ou attribuées à un agent."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    agent_id = inputs.get("agent_id")
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    try:
        db, sc = _scope(workspace_id)
        rows = []
        if task_id is not None:
            rows = sc.sub_tasks.list_for_task(int(task_id))
        elif agent_id:
            aid = int(str(agent_id).split("_")[-1])
            agent_name = f"agent:{aid}" if not str(aid).startswith("agent") else str(aid)
            rows = sc.sub_tasks.list_assigned_to(agent_name)
        else:
            rows = sc.sub_tasks.list_for_task(int(sc.conn.execute(
                "SELECT MAX(task_id) AS t FROM sub_tasks").fetchone()["t"] or 0))
        db.close()
        return {"ok": True, "sub_tasks": rows, "count": len(rows)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def analysis_report(inputs: dict, home: str) -> dict:
    """analysis_report — l'analyste attache son rapport à la tâche analysée.

    Le rapport est TOUJOURS produit (plus ou moins gros) ; son nom reprend la
    tâche analysée. On le stocke dans task_reports (role='analysis') et on le
    dépose dans le dossier commun du workspace si fourni."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    content = (inputs.get("content") or "").strip()
    if not workspace_id or task_id is None or not content:
        return {"ok": False, "error": "workspace_id + task_id + content requis"}
    try:
        db, sc = _scope(workspace_id)
        sc.tasks.add_report(int(task_id), "analysis", content)
        db.close()
        return {"ok": True, "task_id": int(task_id), "role": "analysis"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


# ── Entry/exit du swarm-as-llm (as_llm_leader, SANS LLM) ─────────────────

def create_entry(inputs: dict, home: str) -> dict:
    """create_entry — entrypoint du swarm (as_llm_leader, sans LLM).

    Crée la TASK d'entrée selon le type demandé (chat_entry, completion_entry…)
    + sa sub_task `analysis` initiale unattributed. Le supervisor/l'analyste
    prennent ensuite le relais (découpe → coding/testing/review/merge → respond).

    PREMIÈRE ÉTAPE : si entry_type non fourni, classifie la requête (prompt
    épurée → consensus) en `simple` / `texte` / `code`, puis route :
      - simple → task_type chat_entry (réponse courte, sans découpe code)
      - texte  → task_type completion_entry (rédaction)
      - code   → task_type feature (découpe code)

    inputs :
      - workspace_id, title, description
      - entry_type : chat_entry | completion_entry | feature… (optionnel —
        sinon classifié automatiquement)
      - classify_consensus : bool (défaut true) — classification via consensus
      - priority, team_id
    """
    workspace_id = inputs.get("workspace_id", "")
    title = (inputs.get("title") or "").strip()
    description = (inputs.get("description") or "").strip()
    entry_type = (inputs.get("entry_type") or "").strip()
    priority = int(inputs.get("priority", 0) or 0)
    team_id = int(inputs.get("team_id", -1) or -1)
    # repo/branch de la requête (le swarm-as-llm les pose via run_completion) :
    # les greedy s'en servent pour prepare_workspace (clone + checkout).
    repo = (inputs.get("repo") or "").strip()
    branch = (inputs.get("branch") or "").strip()
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id + title requis"}
    # PREMIÈRE ÉTAPE : classification si entry_type absent.
    classified = ""
    direct_response = ""
    domain = ""
    if not entry_type:
        try:
            from services.skill_manager import call_skill
            _cr = call_skill(
                "classify_entry",
                {"workspace_id": workspace_id,
                 "prompt": f"{title}\n{description}".strip(),
                 "use_consensus": bool(inputs.get("classify_consensus", True)),
                 "n_answering": int(inputs.get("n_answering", 5) or 5)},
                home=home)
            if _cr.get("ok") and _cr.get("type"):
                classified = _cr["type"]
                domain = _cr.get("domain", "")
                # RÉPONSE DIRECTE : la classification a répondu elle-même
                # (requête simple ≤ 1000 chars, pas d'info requise) → on
                # retourne la réponse SANS créer de tâche (le swarm répond).
                if _cr.get("response"):
                    direct_response = _cr["response"]
                entry_type = {
                    "simple": "chat_entry",
                    "texte": "completion_entry",
                    "code": "feature",
                }.get(_cr["type"], "chat_entry")
        except Exception:
            entry_type = entry_type or "chat_entry"
    # Réponse directe de la classification → pas de tâche à découper.
    if direct_response:
        return {"ok": True, "response": direct_response,
                "classified": classified, "route": entry_type,
                "direct": True}
    entry_type = entry_type or "chat_entry"
    try:
        db, sc = _scope(workspace_id)
        task = sc.tasks.create(
            title=title, description=description,
            priority=priority, task_type=entry_type,
            domain=domain, team_id=team_id, primordial=1,
            repo=repo, branch=branch)
        st = sc.sub_tasks.create(
            task_id=task["task_id"], sub_task_type="analysis",
            difficulty="medium", status="unattributed", team_id=team_id,
            description=f"{title}\n{description}".strip())
        db.close()
        return {"ok": True, "task_id": task["task_id"],
                "task": task, "sub_task_id": st["sub_task_id"],
                "classified": classified,
                "domain": domain,
                "route": entry_type}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def entry_result(inputs: dict, home: str) -> dict:
    """entry_result — exitpoint du swarm (as_llm_leader, sans LLM).

    Récupère le livrable final de la tâche : la sub_task `respond` terminale
    (txt + tool_calls + .json). Retourne {ok, response?, status}."""
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    try:
        db, sc = _scope(workspace_id)
        task = sc.tasks.get(int(task_id))
        respond_rows = [s for s in sc.sub_tasks.list_for_task(int(task_id))
                        if s["sub_task_type"] == "respond"]
        response = None
        for s in respond_rows:
            if s["status"] in ("done", "supervised"):
                response = {"tag": s.get("tag", ""),
                            "commit_hash": s.get("commit_hash", "")}
                break
        # La réponse TEXTE du respond (rapport role=respond) → contenu final.
        response_text = ""
        if response:
            try:
                for r in (sc.tasks.get_reports(int(task_id)) or []):
                    # role=respond (nouveau) ou role=work (respond legacy)
                    if r.get("role") in ("respond", "work") and r.get("content"):
                        response_text = str(r["content"]).strip()
                        break
            except Exception:
                pass
            if response_text:
                response["content"] = response_text
        db.close()
        return {"ok": True, "task_status": task.get("status") if task else None,
                "response": response}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def consensus_reponse(inputs: dict, home: str) -> dict:
    """Écrit la réponse d'une answering_machine (table reponse)."""
    workspace_id = inputs.get("workspace_id", "")
    id_question = inputs.get("id_question")
    contenu = (inputs.get("contenu") or "").strip()
    if not workspace_id or not id_question or not contenu:
        return {"ok": False, "error": "workspace_id + id_question + contenu requis"}
    id_agent = inputs.get("id_agent") or ""
    if not id_agent:
        import re
        m = re.search(r"agent_home/(\d+)", home or "")
        id_agent = m.group(1) if m else ""
    try:
        db, sc = _scope(workspace_id)
        q = sc.consensus.get_question(int(id_question))
        if not q:
            db.close()
            return {"ok": False, "error": f"question {id_question} introuvable"}
        r = sc.consensus.add_reponse(
            int(id_question), int(id_agent) if id_agent else 0,
            contenu, model_ref=inputs.get("model_ref", ""))
        db.close()
        return {"ok": True, "id_reponse": r["id_reponse"], "contenu": contenu}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def consensus_ask(inputs: dict, home: str) -> dict:
    """Consensus : pose la question, collecte les réponses, juge.

    Flux (carnet-d-idees.md, Système A) :
      1. crée la question (status awaiting) ;
      2. alloue n_answering modèles DIFFÉRENTS (ask_llm not_same_modele) ;
      3. spawn les answering_machine (une par modèle) avec la question ;
      4. attend les réponses (grace = 1.5× le plus long des N attendues) ;
      5. juge : majorité absolue (2/3 ou 3/5), élimination, escalade.
    Retourne {ok, id_question, consensus, escalade}.
    """
    workspace_id = inputs.get("workspace_id", "")
    question = (inputs.get("question") or "").strip()
    n_answering = int(inputs.get("n_answering", 5) or 5)
    max_tours = int(inputs.get("max_tours", 5) or 5)
    options = inputs.get("options") or []
    if not workspace_id or not question:
        return {"ok": False, "error": "workspace_id + question requis"}
    try:
        db, sc = _scope(workspace_id)
        q = sc.consensus.create_question(question, id_creator=0,
                                         options=options, max_tours=max_tours)
        qid = q["id_question"]
        # 1. Allouer n modèles différents (not_same_modele)
        from services.skill_manager import call_skill
        models = []
        used = []
        for _ in range(n_answering):
            r = call_skill("ask_llm",
                           {"use_case": "coding", "not_same_modele": used},
                           home=home)
            if not r.get("ok") or not r.get("model_ref"):
                break
            models.append({"provider_ref": r.get("provider_ref", ""),
                           "model_ref": r["model_ref"]})
            used = r.get("used_models", used)
        if not models:
            db.close()
            return {"ok": False, "id_question": qid,
                    "error": "aucun modèle alloué pour les answering_machine"}
        n_alloc = len(models)
        # 2. POSER la question à chaque answering_machine (thread parallèle) :
        #    ask_llm_with_prompt → allocation + prompt au bridge + retry sur
        #    échec (un autre modèle). Écrit la réponse via reponse@v1.
        #    Chaque thread utilise used_models CHAINÉ (models[i] déjà alloué) →
        #    on appelle directement avec le modèle alloué, pas re-alloc.
        import threading
        reponses = []
        errors = []

        def _ask_one(i: int, m: dict) -> None:
            try:
                from services.skill_manager import call_skill
                r = call_skill(
                    "ask_llm_with_prompt",
                    {"use_case": "coding",
                     "prompt": f"{question}\nRéponds de façon concise et argumentée.",
                     "system": "answering_machine",
                     "not_same_modele": used[:i],   # différent des autres
                     "max_essais": 3, "timeout": 90},
                    home=home)
                if not r.get("ok") or not r.get("response"):
                    errors.append(f"answering_{i}: {r.get('error', 'vide')}")
                    return
                call_skill(
                    "reponse",
                    {"workspace_id": workspace_id,
                     "id_question": qid,
                     "contenu": r["response"],
                     "id_agent": inputs.get("id_creator") or 0,
                     "model_ref": r.get("model_ref", "")},
                    home=home)
            except Exception as e:
                errors.append(f"answering_{i}: {e}")

        threads = [threading.Thread(target=_ask_one, args=(i, m))
                   for i, m in enumerate(models)]
        for t in threads:
            t.start()
        # 3. Attendre les réponses : grace = 1.5× le plus long des 3 premières
        #    (ici : join avec grace globale — les threads ont chacun un timeout).
        for t in threads:
            t.join()
        reponses = sc.consensus.get_reponses(qid)
        db.close()
        n_ok = sum(1 for r in reponses)
        return {"ok": True, "id_question": qid,
                "n_alloue": n_alloc, "n_reponses": n_ok,
                "errors": errors[:3],
                "models": [m["model_ref"] for m in models],
                "status": "answered" if n_ok else "awaiting",
                "next": "judge",
                "escalade": f"{n_ok} réponses sur {n_alloc} (grace appliquée)"}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def consensus_vote(inputs: dict, home: str) -> dict:
    """VOTE d'un agent au consensus : pose le jugement d'un votant.

    One-pass (l'agent vote une seule fois) :
      - choix = "A"/"B"/"C"… → cible la réponse id_reponse
      - choix = "NEW"       → nouvelle tour
      - choix = autre       → option libre (note 0-1 optionnelle, grade)
    L'agent peut retenter UNE fois si l'appel tool a échoué (retry).
    """
    workspace_id = inputs.get("workspace_id", "")
    id_question = inputs.get("id_question")
    choix = (inputs.get("choix") or "").strip()
    if not workspace_id or not id_question or not choix:
        return {"ok": False, "error": "workspace_id + id_question + choix requis"}
    id_votant = inputs.get("id_votant") or ""
    if not id_votant:
        import re
        m = re.search(r"agent_home/(\d+)", home or "")
        id_votant = m.group(1) if m else ""
    try:
        db, sc = _scope(workspace_id)
        q = sc.consensus.get_question(int(id_question))
        if not q:
            db.close()
            return {"ok": False, "error": f"question {id_question} introuvable"}
        # jugement : A/B/C → "A…" ; NEW → "NEW" ; sinon le choix tel quel.
        jugement = choix
        if id_reponse := inputs.get("id_reponse"):
            # cibler la réponse id_reponse : on vérifie qu'elle existe.
            reps = sc.consensus.get_reponses(int(id_question))
            if not any(str(r["id_reponse"]) == str(id_reponse) for r in reps):
                db.close()
                return {"ok": False, "error": f"réponse {id_reponse} introuvable"}
            # le jugement "A/B/C…" référence l'INDEX de la réponse.
            for i, r in enumerate(reps):
                if str(r["id_reponse"]) == str(id_reponse):
                    jugement = chr(65 + i)
                    break
        # note (grade 0-1) : "note=X" en suffixe du jugement.
        if (note := inputs.get("note")) is not None:
            jugement = f"{jugement}:{float(note):.2f}"
        # Le votant vote : on stocke son jugement (une ligne = un votant).
        # (la reponse du votant lui-même porte son jugement s'il a répondu ;
        # sinon on crée une ligne vote-only.)
        reps = sc.consensus.get_reponses(int(id_question))
        ligne = None
        for r in reps:
            if str(r["id_agent"]) == str(id_votant):
                ligne = r
                break
        if ligne:
            sc.consensus.set_jugement(ligne["id_reponse"], jugement)
        else:
            # votant sans réponse (cancel) : ligne vote-only, contenu vide.
            ligne = sc.consensus.add_reponse(
                int(id_question), int(id_votant) if id_votant else 0,
                "", model_ref=inputs.get("model_ref", ""))
            sc.consensus.set_jugement(ligne["id_reponse"], jugement)
        db.close()
        return {"ok": True, "id_question": int(id_question),
                "id_votant": id_votant, "vote": jugement}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def consensus_judge(inputs: dict, home: str) -> dict:
    """Jugement du consensus : majorité absolue, élimination, escalade.

    Votes : chaque répondant vote A/B/C (réponses) ou NEW (nouvelle tour).
    Règles (carnet-d-idees.md) :
      - majorité absolue : 2/3 si 3 réponses, 3/5 si 5 → consensus.
      - sinon éliminer les moins votées et escalader :
          * NEW autorisé (max max_tours tours) ;
          * puis interdire NEW ;
          * puis interdire de voter pour soi ;
          * puis grader les autres (0-1, sans égalité) → meilleure note ;
          * toujours égalité → au hasard.
    Un modèle peut avoir répondu (cancel) mais voter quand même.
    Retourne {ok, consensus, choix, votes, escalade}.
    """
    workspace_id = inputs.get("workspace_id", "")
    id_question = inputs.get("id_question")
    if not workspace_id or not id_question:
        return {"ok": False, "error": "workspace_id + id_question requis"}
    try:
        import random
        db, sc = _scope(workspace_id)
        q = sc.consensus.get_question(int(id_question))
        if not q:
            db.close()
            return {"ok": False, "error": f"question {id_question} introuvable"}
        reponses = sc.consensus.get_reponses(int(id_question))
        n = len(reponses)
        if n == 0:
            db.close()
            return {"ok": False, "id_question": id_question,
                    "error": "aucune réponse à juger"}
        tour = q.get("tour_courant", 1)
        max_tours = q.get("max_tours", 5) or 5
        options = json.loads(q.get("options_json") or "[]") or []
        choix = [f"{chr(65 + i)}" for i in range(n)]  # A, B, C...
        # 1. Collecter les votes (jugement stocké par chaque votant).
        votes = {}
        for r_ in reponses:
            j = (r_.get("jugement") or "").strip()
            if j:
                votes[r_["id_agent"]] = j
        # vote_soi : autorisé selon l'escalade (tour > max_tours → interdit)
        interdit_soi = tour > max_tours
        # 2. Majorité absolue (2/3 ou 3/5 du total des votants).
        votants = list(votes.keys())
        tot = max(len(votants), 1)
        seuil_abs = 1.0 if n == 1 else ((2 / 3) if n <= 3 else (3 / 5))
        comptes: dict = {}
        for v in votes.values():
            comptes[v] = comptes.get(v, 0) + 1
        # consensus = choix avec majorité absolue, hors NEW.
        consensus = ""
        for choix_, cnt in comptes.items():
            if choix_ != "NEW" and (cnt / tot) >= seuil_abs:
                consensus = choix_
                break
        escalade = ""
        if not consensus:
            # 3. Pas de majorité → escalade.
            if tour <= max_tours:
                escalade = f"NEW autorisé (tour {tour}/{max_tours}) — relancer"
            elif not interdit_soi:
                escalade = "pas de vote pour soi — relancer"
            else:
                # grader les autres (0-1) → meilleure note (simple : la plus
                # fréquente hors soi, sinon hasard).
                counts_autres = {c: cnt for c, cnt in comptes.items()
                                 if c != "NEW"}
                if inputs.get("ask_human"):
                    # escalade HUMAIN demandée : on ne tranche pas par
                    # le hasard, on laisse l'humain décider.
                    consensus = ""
                elif counts_autres:
                    _best = max(counts_autres, key=counts_autres.get)
                    _best_votes = [c for c, cnt in counts_autres.items()
                                   if cnt == counts_autres[_best]]
                    consensus = random.choice(_best_votes) if len(_best_votes) > 1 else _best
                else:
                    consensus = random.choice(choix)
                escalade = f"grade (0-1) → {consensus}" if consensus else \
                    "grade (0-1) — équilibre, escalade humain"
        if consensus and consensus in choix:
            qid = int(id_question)
            sc.consensus.set_status(qid, "answered")
            db.close()
            return {"ok": True, "id_question": qid, "consensus": consensus,
                    "votes": comptes, "n_reponses": n,
                    "escalade": "majorité absolue" if not escalade else escalade,
                    "reponses": [r_["contenu"] for r_ in reponses]}
        # Pas de consensus → on garde la question pour relance (tour suivant).
        qid = int(id_question)
        # ESCALADE HUMAIN : si demandé (ask_human) et plus d'option automatisée,
        # on signale à l'humain via human_choice (issue_id optionnel).
        humain = ""
        if inputs.get("ask_human"):
            try:
                import uuid as _uuid
                _cid = f"hc_{_uuid.uuid4().hex[:12]}"
                _iid = inputs.get("issue_id") or q.get("issue_id")
                db.conn.execute(
                    "INSERT INTO human_choice (choice_id, issue_id, question, "
                    "options_json, status) VALUES (?, ?, ?, ?, 'pending')",
                    (_cid, _iid,
                     f"[consensus {qid}] {q.get('question','')} — pas de "
                     f"majorité (votes {comptes}). Tranche pour les agents.",
                     json.dumps([r_["contenu"] for r_ in reponses])))
                db.conn.commit()
                humain = f"escalade humain (choice_id={_cid})"
                if _iid:
                    db.conn.execute(
                        "UPDATE issues SET status='blocked', updated_at=datetime('now') "
                        "WHERE issue_id = ? AND workspace_id = ?",
                        (_iid, workspace_id))
                    db.conn.commit()
            except Exception:
                humain = "escalade humain (échec human_choice)"
        sc.consensus.set_status(qid, "awaiting")
        db.close()
        return {"ok": False, "id_question": qid, "consensus": "",
                "votes": comptes, "escalade": (escalade or "relancer")
                + (f" | {humain}" if humain else ""),
                "reponses": [r_["contenu"] for r_ in reponses]}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["decoupe", "ask_intel", "ask_new_task", "sub_task_done",
              "sub_task_release", "sub_task_get", "sub_task_list",
              "sub_task_too_hard", "analysis_report", "create_entry",
              "entry_result", "consensus_reponse", "consensus_ask",
              "consensus_judge", "consensus_vote"]
