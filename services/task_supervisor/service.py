"""TaskSupervisor — supervision + attribution du taskflow (V0.15).

Sans LLM, un par team, généraliste mais précisable par un tableau de règles
(team `supervisor_rules`).

Rôles :
  1. Supervision : pour les sub_tasks done/cancelled non supervisées, applique
     la règle (in_type, in_tag) → (out_type, out_tag) : crée la sub_task de
     sortie (dépendance vers la courante) ; out_type vide = noeud terminal →
     supervised. Les sub_tasks waiting_dependencies passent unattributed dès
     que leurs dépendances sont satisfaites. Une tâche (sujet) est supervisée
     quand toutes ses sub_tasks sont closes.
  2. Attribution : lit la file ask_new_task, assigne une sub_task unattributed
     (mapping type↔rôle, niveau max, priorité/âge) ou répond "en attente".
  3. Réveil : déshydratation de l'agent ciblé (le supervisor a le contrôle).

Le chemin nominal est SYNCHRONE (le skill task_ask_new appelle `assign`
pendant le run de l'agent). `supervise_team` est appelé par le tick failsafe.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sql.workspace import WorkspaceDB, WorkspaceScope


class TaskSupervisor:
    def __init__(self, db: Optional[WorkspaceDB] = None):
        self.db = db or WorkspaceDB()

    # ── Réveil par signal (PAS de waker) ──────────────────────────────────
    # Architecture : AUCUN waker de scan. Le seul réveil d'un agent = un
    # SIGNAL `wakeup` posé par le supervisor (table agent_signals), consommé
    # par le FSM de l'agent. Quand une sub_task devient dispo, on réveille un
    # agent du rôle qui la pioche (ROLE_TO_SUBTASK).
    ROLE_TO_SUBTASK = {
        "architecte": "analysis", "planificateur": "analysis",
        "explorateur": "exploration", "explore": "exploration",
        "codeur": "coding", "test_runner": "testing",
        "relecteur": "review", "orchestrateur": "merge",
        "prepare_response": "respond", "consensus": "avis", "avis": "avis",
    }

    def _wake_for_type(self, workspace_id: str, team_id: int,
                       sub_task_type: str) -> int:
        """Pose un signal `wakeup` à un agent du rôle qui pioche ce type.

        Cherche un agent ENDORMI (occupation continue, pas dans runtime, pas de
        thread) dont le rôle traite ce type, dans la team (ou n'importe quelle
        team si team_id=-1). Retourne le nombre de signaux posés."""
        roles = [r for r, st in self.ROLE_TO_SUBTASK.items() if st == sub_task_type]
        if not roles:
            return 0
        ph = ",".join("?" for _ in roles)
        team_filter = "" if team_id == -1 else "AND id_team = ?"
        team_params = () if team_id == -1 else (team_id,)
        try:
            rows = self.db.conn.execute(
                f"SELECT agent_id FROM agents "
                f"WHERE role_type IN ({ph}) {team_filter} "
                f"AND occupation = 'continue' "
                f"AND agent_id NOT IN (SELECT agent_id FROM agent_runtime) "
                f"AND status NOT IN ('TERMINATED', 'STOPPED', 'PAUSED') "
                f"ORDER BY agent_id LIMIT 3",
                (*roles, *team_params)).fetchall()
        except Exception:
            return 0
        n = 0
        for r in rows:
            try:
                from services.agent_manager.service import AgentManager
                AgentManager(db=self.db).send_signal(
                    r["agent_id"], "wakeup",
                    {"type": sub_task_type, "workspace_id": workspace_id})
                n += 1
            except Exception:
                pass
        return n

    # ── Règles ────────────────────────────────────────────────────────────

    def seed_rules(self, workspace_id: str, team_id: int,
                   rules: List[Dict[str, Any]]) -> int:
        """(Re)charge les règles du manifest d'une team en BDD (idempotent).

        Remplace les règles existantes de la team pour cette workspace, puis
        insère la liste fournie. Retourne le nombre de règles actives."""
        sc = self.db.for_workspace(workspace_id)
        sc.conn.execute(
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

    # ── Supervision (une passe) ───────────────────────────────────────────

    def supervise_team(self, workspace_id: str, team_id: int,
                       limit: int = 100) -> Dict[str, Any]:
        """Une passe de supervision pour une team (tick failsafe).

        1. release_dependencies : waiting_dependencies satisfaites →
           unattributed.
        2. apply_rules : sub_tasks done/cancelled non supervisées → crée la
           suivante (règle) + supervised.
        3. finalize_tasks : tâches dont toutes les sub_tasks sont closes →
           supervised (tag du sujet)."""
        sc = self.db.for_workspace(workspace_id)
        created, released, supervised_st, finalized = 0, 0, 0, 0
        bumped, resplit, cancelled = 0, 0, 0

        # 1) Dépendances satisfaites → unattributed + réveil d'un agent du type
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["waiting_dependencies"], limit=limit):
            if sc.sub_tasks.dependencies_satisfied(st["sub_task_id"]):
                sc.sub_tasks.set_status(st["sub_task_id"], "unattributed")
                released += 1
                # Pas de waker : réveille UN agent capable de ce type (signal).
                try:
                    self._wake_for_type(workspace_id, team_id,
                                        st["sub_task_type"])
                except Exception:
                    pass

        # 1bis) TOO_HARD : l'agent a abandonné (doing → too_hard). Le
        # supervisor décide :
        #   - too_hard_count <= MAX_TOO_HARD → bump_difficulty + re-attribution
        #     (unattributed, priorité augmentée pour être re-piocher vite).
        #   - sinon (trop de tentatives) → re-découpe : une sub_task `analysis`
        #     est créée pour que l'analyste découpe autrement (le node too_hard
        #     passe supervised — la découpe repart du sujet).
        MAX_TOO_HARD = 3
        try:
            from modules.sql.workspace import _DIFFICULTY_RANK as _DR
        except Exception:
            _DR = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["too_hard"], limit=limit):
            cnt = int(st.get("too_hard_count") or 0)
            if cnt <= MAX_TOO_HARD:
                # bump difficulté (easy→medium→hard→expert, plafond expert)
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
                    priority=(st.get("priority") or 0) + 5,  # re-piocher vite
                    tag="", freedby="", too_hard_reason="")
                sc.sub_tasks.set_status(st["sub_task_id"], "unattributed")
                bumped += 1
            else:
                # trop de tentatives → re-découpe
                sc.sub_tasks.mark_supervised(st["sub_task_id"])
                sc.sub_tasks.create(
                    task_id=st["task_id"], sub_task_type="analysis",
                    difficulty="medium", status="unattributed",
                    team_id=team_id, priority=10,
                    description=(f"Re-découper la sous-tâche "
                                 f"{st['sub_task_type']} (trop difficile, "
                                 f"{cnt} tentatives) — "
                                 f"{st.get('too_hard_reason', '')}"))
                resplit += 1
        # 1ter) CANCEL : les sub_tasks doing/attributed dont la TÂCHE est
        # annulée (flag cancelled) → envoyer le signal cancel à l'agent assigné.
        # L'agent répond cancelled_done, puis on marque cancelled_supervised.
        try:
            from services.agent_manager.service import AgentManager
            _amgr = AgentManager()
            _cancel_rows = sc.conn.execute("""
                SELECT s.sub_task_id, s.assigned_to, t.task_id
                FROM sub_tasks s JOIN tasks t ON t.task_id = s.task_id
                WHERE s.workspace_id = ? AND s.team_id = ?
                  AND s.status IN ('doing','attributed')
                  AND t.cancelled = 1
                  AND s.assigned_to != ''
            """, (workspace_id, team_id)).fetchall()
            for cr in _cancel_rows:
                _aid = str(cr["assigned_to"]).replace("agent:", "")
                try:
                    _amgr.send_signal(int(_aid), "cancel",
                                      {"task_id": cr["task_id"]})
                except Exception:
                    pass
                cancelled += 1
        except Exception:
            pass
        # 1quater) finaliser les sub_tasks `cancelled_done` → `cancelled_supervised`
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["cancelled_done"], limit=limit):
            sc.sub_tasks.mark_supervised(st["sub_task_id"])
            supervised_st += 1
        # 2) Règles sur les sub_tasks terminées (done/cancelled) non supervisées
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["done", "cancelled"], limit=limit):
            if st.get("supervised"):
                continue
            # Composant d'une découpe (d'autres sub_tasks dépendent de lui, ex.
            # un merge(B,C) attend B et C) : pas de circuit linéaire — le
            # noeud d'aval (merge) gère la suite via les dépendances.
            if sc.sub_tasks.get_children(st["sub_task_id"]):
                sc.sub_tasks.mark_supervised(st["sub_task_id"])
                supervised_st += 1
                continue
            rule = sc.rules.find_rule(st["sub_task_type"], st.get("tag", ""),
                                      workspace_id, team_id)
            if rule and rule.get("out_type"):
                created += self._create_followup(sc, st, rule)
            sc.sub_tasks.mark_supervised(st["sub_task_id"])
            supervised_st += 1

        # 3) Tâches closes → supervised (sujet)
        finalized = self._finalize_closed_tasks(sc, team_id)

        return {"created": created, "released": released,
                "supervised": supervised_st, "tasks_finalized": finalized,
                "too_hard_bumped": bumped, "too_hard_resplit": resplit,
                "cancel_signals": cancelled}

    def _create_followup(self, sc: WorkspaceScope, st: Dict[str, Any],
                         rule: Dict[str, Any]) -> int:
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

    def _finalize_closed_tasks(self, sc: WorkspaceScope, team_id: int) -> int:
        """Tâches dont toutes les sub_tasks sont closes → supervised.

        Pour une tâche d'entrée (chat_entry/completion_entry) : on crée
        d'abord la sub_task `respond` (réponse finale de l'exitpoint) si elle
        n'existe pas — la tâche n'est finalisée que quand le respond est
        terminé."""
        # TTL explorations : une exploration doing depuis > 3 min (l'explorer
        # n'a pas conclu, souvent un intel sur du code inexistant) est libérée
        # en done/ok — sinon elle bloque le respond (waiting_dependencies) et la
        # tâche reste todo pour toujours. NB : updated_at est en ISO avec 'T' →
        # on compare via julianday (pas de comparaison lexicographique naive).
        try:
            import time as _t
            _cutoff_ts = _t.time() - 180
            sc.conn.execute("""
                UPDATE sub_tasks SET status = 'done', tag = 'ok', supervised = 1
                WHERE sub_task_type = 'exploration'
                  AND status = 'doing'
                  AND julianday(updated_at) < julianday(?, 'unixepoch')
            """, (_cutoff_ts,))
            sc.conn.commit()
        except Exception:
            pass
        rows = sc.conn.execute(
            "SELECT task_id FROM sub_tasks WHERE team_id = ? GROUP BY task_id",
            (team_id,)).fetchall()
        n = 0
        for r in rows:
            tid = r["task_id"]
            task = sc.tasks.get(tid)
            if not task or task.get("status") in ("supervised", "done", "cancelled"):
                continue
            stype = (task.get("task_type") or "").lower()
            # Entrées swarm-as-llm : la tâche est finalisée quand le respond est
            # terminé (done/supervised), indépendamment des sub_tasks orphelines
            # (explorations laissées doing par un ask_intel dont l'explorer n'a
            # pas conclu). Sinon la tâche reste todo pour toujours.
            if stype in ("chat_entry", "completion_entry"):
                respond_done = sc.conn.execute(
                    "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                    "AND sub_task_type = 'respond' "
                    "AND status IN ('done','supervised')",
                    (tid,)).fetchone()[0]
                if respond_done:
                    # Clôturer les sub_tasks orphelines restantes (explorations
                    # en doing/unattributed) pour ne pas laisser de résidus.
                    sc.conn.execute(
                        "UPDATE sub_tasks SET supervised = 1, "
                        "status = 'supervised' WHERE task_id = ? "
                        "AND status IN ('doing','unattributed',"
                        "'waiting_dependencies')",
                        (tid,))
                    sc.tasks.update(tid, status="supervised",
                                    tag=task.get("tag") or "ok")
                    sc.conn.execute(
                        "UPDATE sub_tasks SET supervised = 1, "
                        "status = 'supervised' WHERE task_id = ?",
                        (tid,))
                    n += 1
                    continue
                # Pas encore de respond : le créer (réponse finale de
                # l'exitpoint), le prepare-response le piochera.
                has_respond = sc.conn.execute(
                    "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                    "AND sub_task_type = 'respond'",
                    (tid,)).fetchone()[0]
                if not has_respond:
                    sc.sub_tasks.create(
                        task_id=tid, sub_task_type="respond",
                        difficulty="easy", status="unattributed",
                        team_id=team_id,
                        repo=task.get("repo", ""), branch=task.get("branch", ""),
                        # La question est dans la description de la TÂCHE →
                        # le respond la reçoit pour pouvoir répondre.
                        description=(task.get("title") or "") + "\n"
                                    + (task.get("description") or ""))
                    # Pas de waker : réveille un prepare_response (signal).
                    try:
                        _wid = sc.wid or ""
                        self._wake_for_type(_wid, team_id, "respond")
                    except Exception:
                        pass
                continue
            open_ = sc.conn.execute(
                "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                "AND status IN ('unattributed', 'doing', 'waiting_dependencies')",
                (tid,)).fetchone()[0]
            if open_ > 0:
                continue
            # Création du respond pour les entrées du swarm-as-llm.
            if stype in ("chat_entry", "completion_entry"):
                has_respond = sc.conn.execute(
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
                    try:
                        self._wake_for_type(sc.wid or "", team_id, "respond")
                    except Exception:
                        pass
                    continue  # attend le prepare-response
            sc.tasks.update(tid, status="supervised", tag=task.get("tag") or "ok")
            sc.conn.execute(
                "UPDATE sub_tasks SET supervised = 1, status = 'supervised' "
                "WHERE task_id = ?", (tid,))
            n += 1
        return n

    # ── Attribution (synchrone, appelé par task_ask_new) ─────────────────

    def assign(self, workspace_id: str, agent_id: int,
               types: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Attribue une sub_task unattributed à l'agent demandeur.

        - types : [{type, level_max}] — les sub_task_type que l'agent sait
          traiter, avec le niveau max de difficulté.
        - Retourne {ok, sub_task?} — ok=False + reason='wait' si rien de dispo
          (l'agent se déshydrate).
        - Marque la ligne ask_new_task servie (traçabilité)."""
        sc = self.db.for_workspace(workspace_id)
        agent_name = f"agent:{agent_id}" if not str(agent_id).startswith("agent") else str(agent_id)
        # Priorité à ce qui est déjà attribué à l'agent (faux départ/relaunch).
        already = sc.sub_tasks.list_assigned_to(agent_name)
        if already:
            st = sorted(already, key=lambda s: s["updated_at"])[0]
            return self._answer(sc, workspace_id, agent_id, st)

        # Les demandes sont réparties par type + niveau.
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
                # score : priorité de la tâche (haute d'abord) + âge
                task = sc.tasks.get(st["task_id"])
                prio = (task.get("priority", 0) if task else 0)
                st["_score"] = (prio, st["created_at"] or "")
                candidates.append(st)
        if candidates:
            candidates.sort(key=lambda s: s["_score"], reverse=True)
            pick = candidates[0]
            if sc.sub_tasks.assign(pick["sub_task_id"], agent_name):
                return self._answer(sc, workspace_id, agent_id, pick)
        # Rien de dispo → en attente (l'agent se déshydrate).
        self._mark_wait(workspace_id, agent_id)
        return {"ok": False, "reason": "wait",
                "note": "aucune sub_task dispo — en attente"}

    def _answer(self, sc: WorkspaceScope, workspace_id: str,
                agent_id: int, st: Dict[str, Any]) -> Dict[str, Any]:
        self._serve(workspace_id, agent_id)
        return {"ok": True, "sub_task": dict(st),
                "sub_task_id": st["sub_task_id"],
                "task_id": st["task_id"], "type": st["sub_task_type"]}

    def _serve(self, workspace_id: str, agent_id: int) -> None:
        try:
            rows = self.db.for_workspace(workspace_id).ask.list_pending(agent_id)
            for r in rows:
                self.db.for_workspace(workspace_id).ask.serve(r["id"], 0)
        except Exception:
            pass

    def _mark_wait(self, workspace_id: str, agent_id: int) -> None:
        try:
            rows = self.db.for_workspace(workspace_id).ask.list_pending(agent_id)
            for r in rows:
                self.db.for_workspace(workspace_id).ask.mark_wait(r["id"])
        except Exception:
            pass


def _level_rank(level: str) -> int:
    return {"easy": 0, "medium": 1, "hard": 2, "expert": 3}.get(
        str(level or "").strip().lower(), 1)


__all__ = ["TaskSupervisor", "_level_rank"]
