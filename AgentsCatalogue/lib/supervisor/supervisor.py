"""supervisor — agent task_supervisor PAR TEAM (sans LLM).

L'agent supervisor (supervisor.agent.yaml) tourne par team : ses steps
appellent ces skills (assign/finalise/relay/make_respond) qui exécutent la
mécanique unattributed→assigned→doing→done→supervised pour SON workspace, et
posent des signaux wakeup (table agent_signals) aux agents de SA team.

Aucun LLM : SQL + tri + signal. Le AgentManager ne fait QUE réveiller les
agents dont un signal est PENDING (wake_for_signal) — plus AUCUN waker de
scan, plus de TaskSupervisor service global (supprimé).

Identity stable (hors run) :
- team_id = teams.team_id (AUTOINCREMENT, seedée au register du manifest,
  INSERT OR IGNORE par team_ref → jamais renumérotée) ;
- agents.id_team = teams.team_id (posé au register pour TOUS les agents de la
  team) ; agents.ref = manifest agent (identité fichier).
- La "team default" (agents SANS team) = team_id -1 → supervise le workspace
  entier et réveille les agents id_team IS NULL.

Le workspace/team viennent des variables de l'agent (variables_json, posées
au register) : le supervisor supervise avec team_id = -1 pour la team default,
sinon son team_id stable.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

ROLE_TO_SUBTASK = {
    "architecte": "planning", "planificateur": "planning",
    "explorateur": "exploration", "explore": "exploration",
    "codeur": "coding", "test_runner": "testing",
    "relecteur": "reviewing", "orchestrateur": "merging",
    "prepare_response": "respond", "consensus": "avis", "avis": "avis",
}

_SIGNAL_TYPES = {"wakeup", "cancel", "pause", "resume", "sleep", "status",
                 "health", "kill", "configure", "reset"}


def _agent_domain():
    from modules.sqlite.agent.agent import get_domain as _ag
    return _ag()


def _send_signal(agent_id: int, signal_type: str, payload: dict) -> bool:
    """Pose un signal PENDING pour un agent (agent_signals). Direct en base :
    l'agent_manager ne fait QUE les consommer (pas de dépendance de service)."""
    if signal_type not in _SIGNAL_TYPES:
        return False
    try:
        ad = _agent_domain()
        ad.db._conn.execute(
            "INSERT INTO agent_signals (agent_id, type, payload_json, status) "
            "VALUES (?, ?, ?, 'PENDING')",
            (agent_id, signal_type, json.dumps(payload or {})))
        ad.db._conn.commit()
        return True
    except Exception:
        return False


def _scope(inputs: dict, home: str):
    """(workspace_id, team_id) du supervisor : inputs sinon variables de
    l'agent (agent_home/<agent_id> → agents.variables_json)."""
    import re
    workspace_id = (inputs.get("workspace_id") or "").strip()
    team_id = int(inputs.get("team_id", -1) or -1)
    m = re.search(r"agent_home/(\d+)", home or "")
    if m:
        try:
            ad = _agent_domain()
            r = ad.db._conn.execute(
                "SELECT variables_json FROM agents WHERE agent_id = ?",
                (int(m.group(1)),)).fetchone()
            if r:
                v = json.loads(r["variables_json"] or "{}")
                if not workspace_id:
                    workspace_id = v.get("workspace_id", "")
                if team_id == -1:
                    team_id = int(v.get("team_id", -1) or -1)
        except Exception:
            pass
    return workspace_id, team_id


def _wake_for_role(workspace_id: str, team_id: int, sub_task_type: str) -> int:
    """Pose un signal wakeup à UN agent de la team du supervisor qui pioche
    ce type (role_type dans ROLE_TO_SUBTASK). Retourne le nombre de signaux.

    Ciblage par id_team (stable). team_id=-1 = team default → agents SANS team
    (id_team IS NULL)."""
    roles = [r for r, st in ROLE_TO_SUBTASK.items() if st == sub_task_type]
    if not roles:
        return 0
    ph = ",".join("?" for _ in roles)
    if team_id == -1:
        team_cond = "id_team IS NULL"
        team_params: list = []
    else:
        team_cond = "id_team = ?"
        team_params = [team_id]
    try:
        rows = _agent_domain().db._conn.execute(
            f"SELECT agent_id FROM agents "
            f"WHERE role_type IN ({ph}) AND {team_cond} "
            f"AND occupation = 'continue' "
            f"AND agent_id NOT IN (SELECT agent_id FROM agent_runtime) "
            f"AND status NOT IN ('TERMINATED', 'STOPPED', 'PAUSED') "
            f"ORDER BY agent_id LIMIT 3",
            (*roles, *team_params)).fetchall()
        n = 0
        for r in rows:
            if _send_signal(r["agent_id"], "wakeup",
                            {"type": sub_task_type, "workspace_id": workspace_id}):
                n += 1
        return n
    except Exception:
        return 0


# ── Supervision (une passe, par workspace/team) ──────────────────────────

def supervise_team(workspace_id: str, team_id: int = -1,
                   limit: int = 100) -> Dict[str, Any]:
    """Une passe de supervision pour un workspace/team.

    1. release_dependencies : waiting_dependencies satisfaites → unattributed
       + signal wakeup à un agent du type.
    2. sub_tasks unattributed dispo (deps satisfaites) → signal wakeup.
    3. too_hard : bump difficulté (≤3) sinon re-découpe (planning).
    4. cancel : tâche annulée → signal cancel à l'agent assigné.
    5. rules : done/cancelled non supervisées → sub_task de sortie + supervised.
    6. finalize : tâches closes → supervised (création du respond pour les
       entrées swarm-as-llm)."""
    from modules.sqlite.workspace.workspace import WorkspaceDB

    def _wake(st_type: str) -> None:
        try:
            _wake_for_role(workspace_id, team_id, st_type)
        except Exception:
            pass

    wdb = WorkspaceDB()
    try:
        sc = wdb.for_workspace(workspace_id)
        created, released, supervised_st, finalized = 0, 0, 0, 0
        bumped, resplit, cancelled = 0, 0, 0

        # 1) Dépendances satisfaites → unattributed + réveil d'un agent du type
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["waiting_dependencies"], limit=limit):
            if sc.sub_tasks.dependencies_satisfied(st["sub_task_id"]):
                sc.sub_tasks.set_status(st["sub_task_id"], "unattributed")
                released += 1
                _wake(st["sub_task_type"])

        # 1ter) sub_tasks DÉJÀ unattributed (create_entry, découpe…) dispo
        # → réveiller un agent du type (signal). 
        try:
            for st in sc.sub_tasks.list_by_team_status(
                    team_id, ["unattributed"], limit=limit):
                if st.get("supervised"):
                    continue
                if not sc.sub_tasks.dependencies_satisfied(st["sub_task_id"]):
                    continue
                _wake(st["sub_task_type"])
        except Exception:
            pass

        # 1quater) RECLAIM : sub_tasks doing/attributed dont l'agent est MORT
        # (aucune ligne agent_runtime → run terminé/crashé/sweepé sans libérer)
        # → libération + réveil d'un agent du type. Un agent actif a TOUJOURS
        # sa ligne agent_runtime (hydraté AVANT le claim) ; son absence = mort.
        try:
            ad = _agent_domain()
            run_agents = {r["agent_id"] for r in ad.db._conn.execute(
                "SELECT agent_id FROM agent_runtime").fetchall()}
            for st in sc.sub_tasks.list_by_team_status(
                    team_id, ["doing", "attributed"], limit=limit):
                aid = str(st.get("assigned_to") or "")
                if not aid.startswith("agent:"):
                    continue
                try:
                    int_aid = int(aid.split(":")[-1])
                except ValueError:
                    continue
                if int_aid in run_agents:
                    continue
                sc.sub_tasks.release(
                    st["sub_task_id"], freedby="supervisor:reclaim",
                    tag=(st.get("tag") or "") or "reclaim")
                released += 1
                _wake(st["sub_task_type"])
        except Exception:
            pass

        # 1bis) TOO_HARD : bump difficulté (≤3), sinon re-découpe (analysis).
        MAX_TOO_HARD = 3
        try:
            from modules.sqlite.workspace.workspace import _DIFFICULTY_RANK as _DR
        except Exception:
            _DR = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["too_hard"], limit=limit):
            cnt = int(st.get("too_hard_count") or 0)
            if cnt <= MAX_TOO_HARD:
                diff = (st.get("difficulty") or "medium").strip().lower()
                rank = _DR.get(diff, 1)
                new_diff = diff
                if rank < 3:
                    for _d, _r in sorted(_DR.items(), key=lambda x: x[1]):
                        if _r == rank + 1:
                            new_diff = _d
                            break
                sc.sub_tasks.update(
                    st["sub_task_id"],
                    difficulty=new_diff,
                    priority=(st.get("priority") or 0) + 5,
                    tag="", freedby="", too_hard_reason="")
                sc.sub_tasks.set_status(st["sub_task_id"], "unattributed")
                bumped += 1
            else:
                sc.sub_tasks.mark_supervised(st["sub_task_id"])
                sc.sub_tasks.create(
                    task_id=st["task_id"], sub_task_type="planning",
                    difficulty="medium", status="unattributed",
                    team_id=team_id, priority=10,
                    description=(f"Re-découper la sous-tâche "
                                 f"{st['sub_task_type']} (trop difficile, "
                                 f"{cnt} tentatives) — "
                                 f"{st.get('too_hard_reason', '')}"))
                resplit += 1

        # 1ter) CANCEL : sub_tasks doing/attributed dont la TÂCHE est annulée.
        try:
            _cancel_rows = wdb.conn.execute("""
                SELECT s.sub_task_id, s.assigned_to, t.task_id
                FROM sub_tasks s JOIN tasks t ON t.task_id = s.task_id
                WHERE s.workspace_id = ? AND (s.team_id = ? OR ? = -1)
                  AND s.status IN ('doing','attributed')
                  AND t.cancelled = 1
                  AND s.assigned_to != ''
            """, (workspace_id, team_id, team_id)).fetchall()
            for cr in _cancel_rows:
                _aid = str(cr["assigned_to"]).replace("agent:", "")
                try:
                    _send_signal(int(_aid), "cancel", {"task_id": cr["task_id"]})
                except Exception:
                    pass
                cancelled += 1
        except Exception:
            pass

        # 1quater) cancelled_done → cancelled_supervised
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["cancelled_done"], limit=limit):
            sc.sub_tasks.mark_supervised(st["sub_task_id"])
            supervised_st += 1

        # 2) Règles sur les sub_tasks terminées (done/cancelled) non supervisées
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["done", "cancelled"], limit=limit):
            if st.get("supervised"):
                continue
            # Composant d'une découpe (d'autres sub_tasks en dépendent) : le
            # noeud d'aval (merge) gère la suite via les dépendances.
            if sc.sub_tasks.get_children(st["sub_task_id"]):
                sc.sub_tasks.mark_supervised(st["sub_task_id"])
                supervised_st += 1
                continue
            rule = sc.rules.find_rule(st["sub_task_type"], st.get("tag", ""),
                                      workspace_id, team_id)
            if rule and rule.get("out_type"):
                created += _create_followup(wdb, sc, st, rule)
            sc.sub_tasks.mark_supervised(st["sub_task_id"])
            supervised_st += 1

        # 3) Tâches closes → supervised (sujet)
        finalized = _finalize_closed_tasks(wdb, sc, workspace_id, team_id, _wake,
                                           create_respond=True)

        return {"created": created, "released": released,
                "supervised": supervised_st, "tasks_finalized": finalized,
                "too_hard_bumped": bumped, "too_hard_resplit": resplit,
                "cancel_signals": cancelled}
    finally:
        wdb.close()


def _create_followup(wdb, sc, st: Dict[str, Any], rule: Dict[str, Any]) -> int:
    """Crée la sub_task de sortie de la règle (dépend de la courante)."""
    if not rule.get("out_type"):
        return 0
    task = sc.tasks.get(st["task_id"])
    team_id = st.get("team_id", -1)
    repo = st.get("repo", "") or (task.get("repo", "") if task else "")
    branch = st.get("branch", "") or (task.get("branch", "") if task else "")
    new_st = sc.sub_tasks.create(
        task_id=st["task_id"],
        sub_task_type=rule.get("out_type"),
        status="waiting_dependencies",
        tag=rule.get("out_tag", ""),
        difficulty=st.get("difficulty", "medium"),
        repo=repo, branch=branch, team_id=team_id,
    )
    sc.sub_tasks.add_dependency(
        new_st["sub_task_id"], st["sub_task_id"],
        required_state=st["status"], required_tag=st.get("tag", ""))
    return 1


def _finalize_closed_tasks(wdb, sc, workspace_id: str, team_id: int,
                           _wake, create_respond: bool = True) -> int:
    """Tâches dont toutes les sub_tasks sont closes → supervised (sujet).

    Pour une tâche d'entrée (chat_entry/completion_entry) : on crée d'abord la
    sub_task `respond` (réponse finale de l'exitpoint) si elle n'existe pas —
    la tâche n'est finalisée que quand le respond est terminé."""
    # TTL explorations : une exploration doing > 3 min (l'explorer n'a pas
    # conclu) est libérée en done/ok pour ne pas bloquer le respond.
    try:
        import time as _t
        _cutoff_ts = _t.time() - 180
        wdb.conn.execute("""
            UPDATE sub_tasks SET status = 'done', tag = 'ok', supervised = 1
            WHERE sub_task_type = 'exploration'
              AND status = 'doing'
              AND julianday(updated_at) < julianday(?, 'unixepoch')
        """, (_cutoff_ts,))
        wdb.conn.commit()
    except Exception:
        pass
    if team_id == -1:
        team_filter, team_params = "", ()
    else:
        team_filter, team_params = "WHERE s.team_id = ?", (team_id,)
    rows = wdb.conn.execute(
        f"SELECT s.task_id FROM sub_tasks s {team_filter} GROUP BY s.task_id",
        team_params).fetchall()
    n = 0
    for r in rows:
        tid = r["task_id"]
        task = sc.tasks.get(tid)
        if not task or task.get("status") in ("supervised", "done", "cancelled"):
            continue
        stype = (task.get("task_type") or "").lower()
        if stype in ("chat_entry", "completion_entry"):
            respond_done = wdb.conn.execute(
                "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                "AND sub_task_type = 'respond' "
                "AND status IN ('done','supervised')",
                (tid,)).fetchone()[0]
            if respond_done:
                wdb.conn.execute(
                    "UPDATE sub_tasks SET supervised = 1, "
                    "status = 'supervised' WHERE task_id = ? "
                    "AND status IN ('doing','unattributed','attributed',"
                    "'waiting_dependencies')",
                    (tid,))
                sc.tasks.update(tid, status="supervised",
                                tag=task.get("tag") or "ok")
                wdb.conn.execute(
                    "UPDATE sub_tasks SET supervised = 1, "
                    "status = 'supervised' WHERE task_id = ?",
                    (tid,))
                n += 1
                continue
            has_respond = wdb.conn.execute(
                "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                "AND sub_task_type = 'respond'",
                (tid,)).fetchone()[0]
            if not has_respond:
                sc.sub_tasks.create(
                    task_id=tid, sub_task_type="respond",
                    difficulty="easy", status="unattributed",
                    team_id=team_id,
                    repo=task.get("repo", ""), branch=task.get("branch", ""),
                    description=(task.get("title") or "") + "\n"
                                + (task.get("description") or ""))
                _wake("respond")
            continue
        open_ = wdb.conn.execute(
            "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
            "AND status IN ('unattributed', 'attributed', 'doing', "
            "'waiting_dependencies')",
            (tid,)).fetchone()[0]
        if open_ > 0:
            continue
        if stype in ("chat_entry", "completion_entry"):
            has_respond = wdb.conn.execute(
                "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                "AND sub_task_type = 'respond' AND status IN ('done','supervised')",
                (tid,)).fetchone()[0]
            if not has_respond:
                sc.sub_tasks.create(
                    task_id=tid, sub_task_type="respond",
                    difficulty="easy", status="unattributed",
                    team_id=team_id,
                    repo=task.get("repo", ""), branch=task.get("branch", ""),
                    description=(task.get("title") or "") + "\n"
                                + (task.get("description") or ""))
                _wake("respond")
                continue
        sc.tasks.update(tid, status="supervised", tag=task.get("tag") or "ok")
        wdb.conn.execute(
            "UPDATE sub_tasks SET supervised = 1, status = 'supervised' "
            "WHERE task_id = ?", (tid,))
        n += 1
    return n


# ── Attribution (synchrone, appelé par workspace/task_ask_new@v1) ────────

def assign_task(workspace_id: str, agent_id: int,
           types: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Attribue une sub_task unattributed à l'agent demandeur.

    - types : [{type, level_max}] — les sub_task_type que l'agent sait traiter.
    - Retourne {ok, sub_task?} — ok=False + reason='wait' si rien de dispo.
    - Marque la ligne ask_new_task servie (traçabilité)."""
    from modules.sqlite.workspace.workspace import WorkspaceDB
    wdb = WorkspaceDB()
    try:
        sc = wdb.for_workspace(workspace_id)
        agent_name = f"agent:{agent_id}" if not str(agent_id).startswith("agent") else str(agent_id)
        # Priorité à ce qui est déjà attribué à l'agent (faux départ/relaunch).
        already = sc.sub_tasks.list_assigned_to(agent_name)
        if already:
            st = sorted(already, key=lambda s: s["updated_at"])[0]
            return _answer(wdb, sc, workspace_id, agent_id, st)

        candidates: List[Dict[str, Any]] = []
        for t in (types or []):
            stype = t.get("type", "") if isinstance(t, dict) else str(t)
            level_max = t.get("level_max", "") if isinstance(t, dict) else ""
            if not stype:
                continue
            rows = sc.sub_tasks.list_by_type(stype, status="unattributed", limit=20)
            for st in rows:
                if not sc.sub_tasks.dependencies_satisfied(st["sub_task_id"]):
                    continue
                diff = st.get("difficulty", "medium")
                if level_max and _level_rank(diff) > _level_rank(level_max):
                    continue
                task = sc.tasks.get(st["task_id"])
                prio = (task.get("priority", 0) if task else 0)
                st["_score"] = (prio, st["created_at"] or "")
                candidates.append(st)
        if candidates:
            candidates.sort(key=lambda s: s["_score"], reverse=True)
            pick = candidates[0]
            if sc.sub_tasks.assign(pick["sub_task_id"], agent_name):
                return _answer(wdb, sc, workspace_id, agent_id, pick)
        _mark_wait(wdb, workspace_id, agent_id)
        return {"ok": False, "reason": "wait",
                "note": "aucune sub_task dispo — en attente"}
    finally:
        wdb.close()


def _answer(wdb, sc, workspace_id: str, agent_id: int,
            st: Dict[str, Any]) -> Dict[str, Any]:
    _serve(wdb, workspace_id, agent_id)
    # O4 : à l'attribution, on ouvre l'allocation agent→budget pour cette
    # sub_task. Best-effort.
    try:
        from services.llm_allocation.pipeline_budget import (
            open_agent_allocation_for_task)
        open_agent_allocation_for_task(
            None, None, f"agent:{agent_id}", st["sub_task_id"])
    except Exception:
        pass
    return {"ok": True, "sub_task": dict(st),
            "sub_task_id": st["sub_task_id"],
            "task_id": st["task_id"], "type": st["sub_task_type"]}


def _serve(wdb, workspace_id: str, agent_id: int) -> None:
    try:
        sc = wdb.for_workspace(workspace_id)
        for r in sc.ask.list_pending(agent_id):
            sc.ask.serve(r["id"], 0)
    except Exception:
        pass


def _mark_wait(wdb, workspace_id: str, agent_id: int) -> None:
    try:
        sc = wdb.for_workspace(workspace_id)
        for r in sc.ask.list_pending(agent_id):
            sc.ask.mark_wait(r["id"])
    except Exception:
        pass


# ── Règles ───────────────────────────────────────────────────────────────

def seed_rules(workspace_id: str, team_id: int,
               rules: List[Dict[str, Any]]) -> int:
    """(Re)charge les règles du manifest d'une team en BDD (idempotent).

    Remplace les règles existantes de la team pour cette workspace, puis
    insère la liste fournie. team_id=-1 = règles généralistes (workspace)."""
    from modules.sqlite.workspace.workspace import WorkspaceDB
    wdb = WorkspaceDB()
    try:
        sc = wdb.for_workspace(workspace_id)
        wdb.conn.execute(
            "DELETE FROM task_supervisor_rules "
            "WHERE workspace_id = ? AND team_id = ?",
            (workspace_id, team_id))
        n = 0
        for i, r in enumerate(rules or []):
            sc.rules.add_rule(
                in_type=r.get("in_type", ""),
                in_tag=r.get("in_tag", ""),
                out_type=r.get("out_type", ""),
                out_tag=r.get("out_tag", ""),
                team_id=team_id, workspace_id=workspace_id,
                priority=1000 - i,
            )
            n += 1
        return n
    finally:
        wdb.close()


def _level_rank(level: str) -> int:
    return {"easy": 0, "medium": 1, "hard": 2, "expert": 3}.get(
        str(level or "").strip().lower(), 1)


# ── Skills de l'agent supervisor (steps) ─────────────────────────────────

def _supervise(inputs: dict, home: str) -> dict:
    ws, team = _scope(inputs, home)
    if not ws:
        return {"ok": False, "error": "workspace_id requis",
                "released": 0, "supervised": 0, "created": 0}
    return supervise_team(ws, team)


def assign(inputs: dict, home: str) -> dict:
    """Ordonnancement : unattributed → attributed (attribution)."""
    return _supervise(inputs, home)


def finalise(inputs: dict, home: str) -> dict:
    """Finalise : tasks closes → supervised."""
    return _supervise(inputs, home)


def relay(inputs: dict, home: str) -> dict:
    """Relais : waiting_dependencies satisfaites → unattributed."""
    return _supervise(inputs, home)


def make_respond(inputs: dict, home: str) -> dict:
    """Crée la sub_task respond + réveille un prepare_response (signal)."""
    return _supervise(inputs, home)


__skills__ = ["assign", "finalise", "relay", "make_respond"]