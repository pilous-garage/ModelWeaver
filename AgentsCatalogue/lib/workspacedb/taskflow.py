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
}


def _scope(workspace_id: str):
    db = WorkspaceDB()
    return db, db.for_workspace(workspace_id)


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
    """Découpe A → B, C, … + merge auto (skill analysis/decoupe@v1).

    La sub_task analysis courante de `task_id` passe en waiting_dependencies.
    Pour chaque sous-tâche déclarée (type = niveau de nécessité : analysis,
    coding, testing, review…), une sub_task unattributed est créée. Un noeud
    `merge(B,C,…)` est généré automatiquement (dépend de toutes les sous-tâches).
    La sub_task analysis courante dépend du merge → elle ne redevient
    unattributed (re-analysis post-merge) que quand le merge est terminé.

    Le `rapport_analysis` est attaché à la tâche (task_reports).
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    sous = inputs.get("sous_taches") or []
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
    if not isinstance(sous, list) or not sous:
        return {"ok": False, "error": "sous_taches : liste non vide requise"}
    try:
        db, sc = _scope(workspace_id)
        task = sc.tasks.get(int(task_id))
        if not task:
            db.close()
            return {"ok": False, "error": "tâche introuvable"}
        team_id = task.get("team_id", -1)
        repo = task.get("repo", "") or ""
        branch = task.get("branch", "") or ""

        cur = _current_analysis(db, sc, task_id)

        created_ids: List[int] = []
        for i, st in enumerate(sous):
            title = (st.get("title") or "").strip()
            stype = (st.get("type") or "").strip().lower()
            if not title or not stype:
                db.close()
                return {"ok": False,
                        "error": f"sous-tâche {i}: title + type requis"}
            new_st = sc.sub_tasks.create(
                task_id=int(task_id), sub_task_type=stype,
                difficulty=st.get("difficulty") or "medium",
                status="unattributed",
                repo=repo, branch=branch, team_id=team_id)
            created_ids.append(new_st["sub_task_id"])

        # Merge automatique : dépend de toutes les sous-tâches.
        merge_st = sc.sub_tasks.create(
            task_id=int(task_id), sub_task_type="merge",
            difficulty="medium", status="waiting_dependencies",
            repo=repo, branch=branch, team_id=team_id)
        for cid in created_ids:
            sc.sub_tasks.add_dependency(merge_st["sub_task_id"], cid,
                                        "done", "")

        # La sub_task analysis courante attend le merge.
        if cur:
            sc.sub_tasks.waiting_dependencies(cur["sub_task_id"])
            sc.sub_tasks.add_dependency(cur["sub_task_id"],
                                        merge_st["sub_task_id"], "done", "ok")

        # Rapport d'analyse attaché à la tâche.
        rapport = (inputs.get("rapport_analysis") or "").strip()
        if rapport:
            try:
                sc.tasks.add_report(int(task_id), "analysis", rapport)
            except Exception:
                pass
        db.close()
        return {"ok": True, "created": created_ids, "count": len(created_ids),
                "merge_sub_task_id": merge_st["sub_task_id"],
                "analysis_sub_task_id": cur["sub_task_id"] if cur else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
    if not workspace_id or not agent_id:
        return {"ok": False, "error": "workspace_id + agent_id requis"}
    try:
        aid = int(str(agent_id).split("_")[-1])
    except (ValueError, TypeError):
        aid = 0
    try:
        db, sc = _scope(workspace_id)
        # 1) Déjà attribuées à l'agent → on les reprend en priorité.
        agent_name = f"agent:{aid}" if not str(aid).startswith("agent") else str(aid)
        already = sc.sub_tasks.list_assigned_to(agent_name)
        if already:
            st = sorted(already, key=lambda s: s["updated_at"])[0]
            db.close()
            return {"ok": True, "sub_task": dict(st),
                    "sub_task_id": st["sub_task_id"],
                    "task_id": st["task_id"], "type": st["sub_task_type"]}
        # 2) Écrit la demande en BDD, puis appel synchrone au supervisor.
        ask_id = sc.ask.create(aid, types)
        db.close()
        from services.task_supervisor.service import TaskSupervisor
        sup = TaskSupervisor()
        res = sup.assign(workspace_id, aid, types)
        if res.get("ok"):
            return {"ok": True, "sub_task": res["sub_task"],
                    "sub_task_id": res["sub_task_id"],
                    "task_id": res["task_id"], "type": res["type"],
                    "ask_id": ask_id}
        return {"ok": False, "reason": "wait", "ask_id": ask_id,
                "note": res.get("note", "aucune sub_task dispo")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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

    inputs :
      - workspace_id, title, description
      - entry_type : chat_entry | completion_entry | feature…
      - priority, team_id
    """
    workspace_id = inputs.get("workspace_id", "")
    title = (inputs.get("title") or "").strip()
    description = (inputs.get("description") or "").strip()
    entry_type = (inputs.get("entry_type") or "chat_entry").strip()
    priority = int(inputs.get("priority", 0) or 0)
    team_id = int(inputs.get("team_id", -1) or -1)
    if not workspace_id or not title:
        return {"ok": False, "error": "workspace_id + title requis"}
    try:
        db, sc = _scope(workspace_id)
        task = sc.tasks.create(
            title=title, description=description,
            priority=priority, task_type=entry_type,
            team_id=team_id, primordial=1)
        st = sc.sub_tasks.create(
            task_id=task["task_id"], sub_task_type="analysis",
            difficulty="medium", status="unattributed", team_id=team_id)
        db.close()
        return {"ok": True, "task_id": task["task_id"],
                "task": task, "sub_task_id": st["sub_task_id"]}
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
                response = {"tag": s.get("tag", ""), "commit_hash": s.get("commit_hash", "")}
                break
        db.close()
        return {"ok": True, "task_status": task.get("status") if task else None,
                "response": response}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["decoupe", "ask_new_task", "sub_task_done", "sub_task_release",
              "sub_task_get", "sub_task_list", "analysis_report",
              "create_entry", "entry_result"]
