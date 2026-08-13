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

        # 1) Dépendances satisfaites → unattributed
        for st in sc.sub_tasks.list_by_team_status(
                team_id, ["waiting_dependencies"], limit=limit):
            if sc.sub_tasks.dependencies_satisfied(st["sub_task_id"]):
                sc.sub_tasks.set_status(st["sub_task_id"], "unattributed")
                released += 1

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
                "supervised": supervised_st, "tasks_finalized": finalized}

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
        rows = sc.conn.execute(
            "SELECT task_id FROM sub_tasks WHERE team_id = ? GROUP BY task_id",
            (team_id,)).fetchall()
        n = 0
        for r in rows:
            tid = r["task_id"]
            open_ = sc.conn.execute(
                "SELECT COUNT(*) FROM sub_tasks WHERE task_id = ? "
                "AND status IN ('unattributed', 'doing', 'waiting_dependencies')",
                (tid,)).fetchone()[0]
            task = sc.tasks.get(tid)
            if not task or task.get("status") in ("supervised", "done", "cancelled"):
                continue
            if open_ > 0:
                continue
            # Création du respond pour les entrées du swarm-as-llm.
            stype = (task.get("task_type") or "").lower()
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
                        repo=task.get("repo", ""), branch=task.get("branch", ""))
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
            if sc.sub_tasks.claim(pick["sub_task_id"], agent_name):
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
