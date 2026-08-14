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
    """Découpe en UNE passe (skill analysis/decoupe@v1) — tool UNIQUE.

    Deux cas :
      - `sous_taches` non vide : découpe A → B,C,… + merge auto. La sub_task
        analysis courante passe waiting_dependencies du merge.
      - `sous_taches` vide (rien à découper) : assigne simplement la difficulté
        (inputs.difficulty) et clôt l'analyse (sub_task analysis → done/ok).

    `analyse` (le rapport d'analyse, toujours produit) est enregistré dans
    task_reports. Le merge n'est jamais déclaré explicitement : il est généré
    automatiquement (dépend de toutes les sous-tâches).
    """
    workspace_id = inputs.get("workspace_id", "")
    task_id = inputs.get("task_id")
    sous = inputs.get("sous_taches") or []
    if not workspace_id or task_id is None:
        return {"ok": False, "error": "workspace_id + task_id requis"}
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

        # ── Cas SIMPLE : rien à découper → assigner difficulté + clore ──
        if not sous:
            diff = (inputs.get("difficulty") or "").strip().lower()
            if diff not in ("easy", "medium", "hard", "expert"):
                diff = task.get("difficulty") or "medium"
            # assigner la difficulté de la tâche + clore l'analyse done/ok
            sc.tasks.update(int(task_id), difficulty=diff)
            if cur:
                sc.sub_tasks.update(cur["sub_task_id"], difficulty=diff)
                sc.sub_tasks.set_status(cur["sub_task_id"], "done", tag="ok")
            _save_analyse(sc, task_id, inputs)
            db.close()
            return {"ok": True, "mode": "assign_difficulte",
                    "difficulty": diff,
                    "analysis_sub_task_id": cur["sub_task_id"] if cur else None}

        # ── Cas DÉCOUPE : A → B,C,… + merge auto ──
        if not isinstance(sous, list):
            db.close()
            return {"ok": False, "error": "sous_taches : liste requise"}
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
                difficulty=st.get("difficulty") or st.get("niveau") or "medium",
                status="unattributed",
                repo=repo, branch=branch, team_id=team_id,
                description=(f"{title}\n{st.get('description', '')}").strip())
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

        _save_analyse(sc, task_id, inputs)
        db.close()
        return {"ok": True, "mode": "decoupe", "created": created_ids,
                "count": len(created_ids),
                "merge_sub_task_id": merge_st["sub_task_id"],
                "analysis_sub_task_id": cur["sub_task_id"] if cur else None}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def ask_intel(inputs: dict, home: str) -> dict:
    """ask_intel — demande d'info au sous-agent EXPLORER (tool de l'analyste).

    L'analyste précise les intels demandés (questions/éléments à regarder). Le
    skill crée une sub_task `exploration` (unattributed) avec la liste des
    intels ; la sub_task analysis passe waiting_dependencies de l'exploration.
    Quand l'exploration est done (rapport produit), l'analysis redevient
    unattributed → l'analyste la reprend avec les nouvelles infos.
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
        # Rien de dispo → l'agent se déshydrate, mais on l'enregistre en
        # wait_for (sub_task_available) pour que le waker le réveille quand une
        # sub_task de son type devient dispo.
        _register_wait(workspace_id, aid, types)
        return {"ok": False, "reason": "wait", "ask_id": ask_id,
                "note": res.get("note", "aucune sub_task dispo")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


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
            role = "exploration" if st["sub_task_type"] == "exploration" else "work"
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


__skills__ = ["decoupe", "ask_intel", "ask_new_task", "sub_task_done",
              "sub_task_release", "sub_task_get", "sub_task_list",
              "analysis_report", "create_entry", "entry_result"]
