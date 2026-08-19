"""workspace — domaine sqlite workspace (portage de modules/sql_old/workspace.py).

WorkspaceDB + repos (WorkspaceRepository, TaskRepository, SubTaskRepository,
AskNewTaskRepository, SupervisorRulesRepository, ConsensusRepository,
IssueRepository, ChatroomRepository, UsageFileRepository…) branchés sur le
domaine modules.sqlite.workspace (workspace.db, schéma versionné, autocommit
WAL via base.py). Aucun SQL hors modules/sqlite.

API identique au legacy : db.workspaces / db.config / db.for_workspace(w) avec
.tasks / .issues / .chat / .usage / .sub_tasks / .ask / .rules / .consensus.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path
from modules.sqlite.workspace import db as workspace_db


def _default_workspace_db() -> Path:
    """Compat : ~/.modelweaver/workspace.db (même fichier que le domaine)."""
    return db_path("workspace")


def _row(row):
    return dict(row) if row else None


def _rows(rows):
    return [dict(r) for r in rows]


_LEVEL_RANK = {"junior": 0, "mid": 1, "senior": 2}
_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


def _compatible_roles(role_required: str) -> List[str]:
    """Rôles compatibles pour un rôle demandé (hiérarchie de capabilité).

    Ex. 'coder_senior' → [coder_senior, coder_mid, coder_junior].
    Ex. 'tester' (sans niveau) → [tester]. Ex. '' → [] (toutes tâches).
    """
    if not role_required:
        return []
    if "_" not in role_required:
        return [role_required]
    base, level = role_required.rsplit("_", 1)
    rank = _LEVEL_RANK.get(level)
    if rank is None:
        return [role_required]
    levels = [lv for lv, r in sorted(_LEVEL_RANK.items(), key=lambda x: x[1])
              if r <= rank]
    return [f"{base}_{lv}" for lv in levels]


class WorkspaceRepository:
    """Méta des workspaces (liste légère, pas de chargement lourd)."""

    def __init__(self, db: Db):
        self._db = db

    def list(self) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT workspace_id, name, description, director, "
            "git_shared, created_at, last_activity_at "
            "FROM workspaces ORDER BY last_activity_at DESC"
        ).fetchall())

    def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace_id,)
        ).fetchone())

    def create(self, workspace_id: str, name: str, description: str = "",
               director: Optional[str] = None, git_shared: str = "") -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._db._conn.execute("""
            INSERT INTO workspaces (workspace_id, name, description, director,
                                    git_shared, created_at, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id, name, description, director, git_shared, now, now))
        self._db._conn.commit()
        return self.get(workspace_id)

    def touch(self, workspace_id: str) -> None:
        self._db._conn.execute(
            "UPDATE workspaces SET last_activity_at = ? WHERE workspace_id = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), workspace_id))
        self._db._conn.commit()

    def delete(self, workspace_id: str) -> None:
        self._db._conn.execute("DELETE FROM workspaces WHERE workspace_id = ?",
                          (workspace_id,))
        self._db._conn.commit()


class WorkspaceConfigRepository:
    """Configuration clef/valeur par workspace."""

    def __init__(self, db: Db):
        self._db = db

    def get(self, workspace_id: str, key: str) -> Optional[str]:
        row = self._db._conn.execute(
            "SELECT value FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key)).fetchone()
        return row[0] if row else None

    def set(self, workspace_id: str, key: str, value: str) -> None:
        self._db._conn.execute("""
            INSERT OR REPLACE INTO workspace_config (workspace_id, key, value)
            VALUES (?, ?, ?)
        """, (workspace_id, key, value))
        self._db._conn.commit()

    def delete(self, workspace_id: str, key: str) -> None:
        self._db._conn.execute(
            "DELETE FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key))
        self._db._conn.commit()

    def all(self, workspace_id: str) -> Dict[str, str]:
        rows = self._db._conn.execute(
            "SELECT key, value FROM workspace_config WHERE workspace_id = ?",
            (workspace_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}


class TaskRepository:
    """Tâches d'un workspace."""

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def list_pending(self) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? AND status = 'pending' "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? "
            "ORDER BY status, priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND workspace_id = ?",
            (task_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0,
               difficulty: str = "medium", task_type: str = "",
               domain: str = "",
               team_id: int = -1, repo: str = "",
               branch: str = "", base_commit: str = "",
               commit_start: str = "", branch_start: str = "",
               primordial: int = 0,
               deadline: str = "", estimated_minutes: int = 0,
               created_at_iso: str = None) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        # created_at au format SQLite (YYYY-MM-DD HH:MM:SS) pour que
        # strftime('%s') du score de priorité le parse (le format iso avec T
        # + microsecondes renvoie NULL). Une sous-tâche (split) hérite du
        # created_at de sa primordiale (anti-famine depuis la mission).
        created = created_at_iso or now.replace("T", " ").split(".")[0]
        cs = commit_start or base_commit or ""
        bs = branch_start or branch or ""
        cur = self._db._conn.execute("""
            INSERT INTO tasks (workspace_id, title, description, priority,
                               status, difficulty, task_type, domain, team_id,
                               repo, branch, base_commit,
                               commit_start, branch_start, primordial,
                               deadline, estimated_minutes,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority,
              difficulty, task_type, domain, team_id, repo, branch, base_commit,
              cs, bs, primordial, deadline, estimated_minutes,
              created, now))
        self._db._conn.commit()
        return self.get(cur.lastrowid)

    def add_dependency(self, child_id: int, parent_id: int,
                       required_state: str = "done") -> bool:
        """Lie un enfant à un parent : l'enfant n'est piochable que si le
        parent est dans l'état requis (dépendance totale). Idempotent."""
        self._db._conn.execute("""
            INSERT OR IGNORE INTO task_dependencies
                (task_id, parent_id, required_state)
            VALUES (?, ?, ?)
        """, (child_id, parent_id, required_state))
        self._db._conn.commit()
        return True

    def get_parents(self, task_id: int) -> List[Dict[str, Any]]:
        """Parents de la tâche (dont elle dépend) avec leur état requis."""
        return _rows(self._db._conn.execute(
            "SELECT d.parent_id, d.required_state, p.status, p.task_type, p.title "
            "FROM task_dependencies d JOIN tasks p ON p.task_id = d.parent_id "
            "WHERE d.task_id = ?", (task_id,)).fetchall())

    def get_children(self, task_id: int) -> List[Dict[str, Any]]:
        """Enfants de la tâche (ceux qui dépendent d'elle) — la filiation."""
        return _rows(self._db._conn.execute(
            "SELECT d.task_id, d.required_state, t.status, t.task_type, "
            "t.title, t.primordial FROM task_dependencies d "
            "JOIN tasks t ON t.task_id = d.task_id "
            "WHERE d.parent_id = ?", (task_id,)).fetchall())

    def get_descendants(self, task_id: int) -> List[int]:
        """Tous les descendants (filiation récursive) de la tâche, ordre feuille
        d'abord. La tâche elle-même n'est pas incluse."""
        out: List[int] = []
        seen = set()
        stack = [child["task_id"] for child in self.get_children(task_id)]
        while stack:
            cid = stack.pop()
            if cid in seen:
                continue
            seen.add(cid)
            out.append(cid)
            stack.extend(ch["task_id"] for ch in self.get_children(cid))
        return out

    def get_ancestors(self, task_id: int) -> List[int]:
        """Tous les ANCÊTRES (parents, et leurs parents, récursivement) d'une
        tâche. Pour un merge_split, ce sont les travaux splittés (B,C) dont il
        dépend. Racine d'abord, la tâche elle-même n'est pas incluse."""
        out: List[int] = []
        seen = set()
        stack = [p["parent_id"] for p in self.get_parents(task_id)]
        while stack:
            pid = stack.pop()
            if pid in seen:
                continue
            seen.add(pid)
            out.append(pid)
            stack.extend(p["parent_id"] for p in self.get_parents(pid))
        return out

    def get_ancestors_with_state(self, task_id: int) -> List[Dict[str, Any]]:
        """Ancêtres de la tâche avec leur état + nature (primordiale ?)."""
        out: List[Dict[str, Any]] = []
        for pid in self.get_ancestors(task_id):
            row = self.get(pid)
            if row:
                out.append(row)
        return out

    def _parents_blocked_sql(self) -> str:
        """Sous-requête : la tâche t est BLOQUÉE si un de ses parents n'est pas
        dans l'état requis (dépendance totale : TOUS les parents requis)."""
        return ("""
            NOT EXISTS (
                SELECT 1 FROM task_dependencies d
                WHERE d.task_id = t.task_id
                  AND (SELECT status FROM tasks p WHERE p.task_id = d.parent_id)
                      != d.required_state
            )
        """)

    def assigned_to_agent(self, assigned_to: str,
                          task_types: Optional[list] = None) -> List[Dict[str, Any]]:
        """Toutes les tâches (todo/doing/done/cancelled) actuellement assignées
        à un agent, filtrées par task_types si fourni. Utilisé par le picker
        pour libérer les tâches terminées/annulées encore assignées."""
        if not assigned_to:
            return []
        ph = ",".join("?" for _ in (task_types or [])) if task_types else None
        sql = ("SELECT * FROM tasks WHERE workspace_id = ? "
               "AND assigned_to = ?")
        args: list = [self.wid, str(assigned_to)]
        if ph:
            sql += f" AND task_type IN ({ph})"
            args.extend(task_types or [])
        rows = self._db._conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows] if rows else []

    def claim_resume(self, task_types: List[str] = (),
                     assigned_to: str = "") -> Optional[Dict[str, Any]]:
        """Recharge une tâche 'doing' DÉJÀ assignée à l'agent (reprise de run).

        Un greedy réveillé peut avoir pioché une tâche au run précédent puis
        avoir été coupé (crash/redémarrage) : la tâche reste 'doing' assignée.
        Sans reprise, il re-pioche → échoue (déjà doing) → dort → la tâche
        reste bloquée en doing pour toujours. Ici on la recharge telle quelle."""
        if not task_types or not assigned_to:
            return None
        ph = ",".join("?" for _ in task_types)
        row = self._db._conn.execute(
            f"SELECT * FROM tasks WHERE workspace_id = ? "
            f"AND status = 'doing' AND assigned_to = ? "
            f"AND COALESCE(cancelled, 0) = 0 "
            f"AND task_type IN ({ph}) "
            f"ORDER BY task_id LIMIT 1",
            (self.wid, str(assigned_to), *task_types)).fetchone()
        if row is None:
            return None
        return dict(row) if hasattr(row, "keys") else None

    def claim_next(self, task_types: List[str] = (),
                   max_difficulty: Dict[str, str] = None,
                   exclude_assigned: tuple = (),
                   team_id: int = -1,
                   assigned_to: str = "",
                   accept_external_work: bool = True,
                   now_minutes: float = None) -> Optional[Dict[str, Any]]:
        """Pioche le prochain token piochable (greedy) pour les task_types donnés.

        Un agent pioche les tokens dont le task_type est dans `task_types` et
        dont la difficulté est ≤ `max_difficulty[task_type]` (le niveau de
        l'agent borne la difficulté piochable par type). Dépendance totale :
        un token n'est piochable que si TOUS ses parents sont dans l'état
        requis (task_dependencies). Statut cible : 'todo' → 'doing'.

        SCOPE : si `accept_external_work` est False, l'agent ne pioche QUE les
        tâches de SA team (team_id exact) — jamais les tâches projet partagé
        (-1). Si True (défaut), il pioche sa team ET les tâches -1, en
        priorité SA team d'abord.

        PRIORITÉ : score à la volée (calculé dans l'ORDER BY) =
          base_priority
          + W_DEADLINE * urgence (0..10)  # temps long + deadline courte → 10
          + W_AGE * age_minutes           # anti-famine (depuis la primordiale)
        Une tâche EN RETARD (remaining <= 0) a urgence = 10 immédiatement.
        Atomique.
        """
        task_types = [t for t in (task_types or []) if t]
        if not task_types:
            return None
        max_diff = max_difficulty or {}
        if now_minutes is None:
            import time
            now_minutes = time.time() / 60.0
        sel_args = [self.wid]
        ph = ",".join("?" for _ in task_types)
        sel = ("SELECT t.* FROM tasks t WHERE t.workspace_id = ? "
               "AND t.status = 'todo' AND COALESCE(t.cancelled, 0) = 0 "
               "AND t.task_type IN (" + ph + ")")
        sel_args.extend(task_types)
        # Difficulté maximale piochable par type (le niveau de l'agent).
        diff_conds = []
        for tt in task_types:
            mx = max_diff.get(tt)
            if mx and mx in _DIFFICULTY_RANK:
                diff_conds.append(
                    f"(t.task_type = ? AND {_DIFFICULTY_RANK[mx]} >= "
                    f"CASE t.difficulty WHEN 'easy' THEN 0 WHEN 'medium' THEN 1 "
                    f"WHEN 'hard' THEN 2 WHEN 'expert' THEN 3 ELSE 1 END)")
                sel_args.append(tt)
        if diff_conds:
            sel += " AND (" + " OR ".join(diff_conds) + ")"
        # Scope : SA team (+ -1 si accept_external_work). Une tâche sans team
        # (-1) n'est jamais appropriée par une team (team_id inchangé).
        if accept_external_work:
            sel += " AND (t.team_id = ? OR t.team_id = -1)"
        else:
            sel += " AND t.team_id = ?"
        sel_args.append(team_id)
        # Dépendance totale : tous les parents à l'état requis.
        sel += " AND " + self._parents_blocked_sql()
        if exclude_assigned:
            ph2 = ",".join("?" for _ in exclude_assigned)
            sel += f" AND COALESCE(t.assigned_to,'') NOT IN ({ph2})"
            sel_args.extend(exclude_assigned)
        # ── Score de priorité (à la volée, en minutes) ──
        # remaining = deadline_minutes - now ; urgence : retard → 10, sinon
        # clamp(estimated / max(remaining,1), 0, 10). W_DEADLINE=100, W_AGE=0.1.
        # L'âge part de created_at (la primordiale : les sous-tâches héritent
        # du created_at de leur mission, pas de leur propre création).
        sel += (
            " ORDER BY CASE WHEN t.team_id = ? THEN 0 ELSE 1 END, "
            "(t.priority"
            "  + 100 * (CASE"
            "      WHEN t.deadline IS NULL OR t.deadline = '' THEN 0"
            "      WHEN (strftime('%s', t.deadline) / 60.0) - ? <= 0 THEN 10"
            "      ELSE MIN(10, (COALESCE(t.estimated_minutes, 0) * 1.0)"
            "                  / MAX((strftime('%s', t.deadline) / 60.0) - ?, 1))"
            "    END)"
            "  + MIN(100, 0.1 * MAX((? - (strftime('%s', t.created_at) / 60.0)), 0))"
            ") DESC, t.created_at ASC LIMIT 1")
        sel_args.extend([team_id, now_minutes, now_minutes, now_minutes])
        row = _row(self._db._conn.execute(sel, sel_args).fetchone())
        if not row:
            return None
        cur = self._db._conn.execute(
            "UPDATE tasks SET status = 'doing', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'todo'",
            (assigned_to, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             row["task_id"], self.wid))
        self._db._conn.commit()
        return self.get(row["task_id"]) if cur.rowcount else None

    def update(self, task_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get(task_id)
        sets.append("updated_at = ?")
        vals.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        vals.extend([task_id, self.wid])
        self._db._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self._db._conn.commit()
        return self.get(task_id)

    def claim(self, task_id: int, agent_name: str) -> bool:
        cur = self._db._conn.execute(
            "UPDATE tasks SET status = 'running', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'pending'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), task_id, self.wid))
        self._db._conn.commit()
        return cur.rowcount > 0

    def done(self, task_id: int, branch: str = "",
             commit_hash: str = "") -> Optional[Dict[str, Any]]:
        self._db._conn.execute("""
            UPDATE tasks SET status = 'done', branch = ?, commit_hash = ?,
                            updated_at = ?
            WHERE task_id = ? AND workspace_id = ?
        """, (branch, commit_hash, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
              task_id, self.wid))
        self._db._conn.commit()
        return self.get(task_id)

    def set_status(self, task_id: int, status: str,
                   branch: str = "", commit_hash: str = "",
                   assigned_to: str = "") -> Optional[Dict[str, Any]]:
        """Passe un token à un statut arbitraire (todo/doing/done/merged...)."""
        sets = ["status = ?", "updated_at = ?"]
        vals = [status, datetime.now(timezone.utc).replace(tzinfo=None).isoformat()]
        if branch:
            sets.append("branch = ?")
            vals.append(branch)
        if commit_hash:
            sets.append("commit_hash = ?")
            vals.append(commit_hash)
        if assigned_to:
            sets.append("assigned_to = ?")
            vals.append(assigned_to)
        vals.extend([task_id, self.wid])
        self._db._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self._db._conn.commit()
        return self.get(task_id)

    def modify(self, task_id: int, new_task_type: str = None,
               status: str = None, branch: str = "",
               commit_hash: str = "", assigned_to: str = None,
               clear_assigned: bool = False,
               commit_start: str = None, branch_start: str = None,
               cancelled: int = None) -> Optional[Dict[str, Any]]:
        """Transition de token vers l'étape suivante du pipeline.

        Conceptuellement DÉTRUIT le token courant et CRÉE le token suivant
        (même task_id : on change task_type + status). Ex. fin d'un coding :
        modify(task, new_task_type='code_review', status='todo') → le token
        devient un code_review piochable par les reviewers.

        `assigned_to=None` : ne touche pas l'assignation. `clear_assigned=True` :
        libère le token (fin de traitement → nouveau token à piocher).
        `commit_start`/`branch_start` : fixés par le 1er picker (déduits du
        clone). `cancelled` : flag d'annulation (cancel doux).
        """
        sets = []
        vals = []
        if new_task_type is not None:
            sets.append("task_type = ?")
            vals.append(new_task_type)
        if status is not None:
            sets.append("status = ?")
            vals.append(status)
        if branch:
            sets.append("branch = ?")
            vals.append(branch)
        if commit_hash:
            sets.append("commit_hash = ?")
            vals.append(commit_hash)
        # Tracking git : la transition met à jour la position courante.
        if commit_hash:
            sets.append("commit_current = ?")
            vals.append(commit_hash)
        if branch:
            sets.append("branch_current = ?")
            vals.append(branch)
        if commit_start is not None:
            sets.append("commit_start = ?")
            vals.append(commit_start)
        if branch_start is not None:
            sets.append("branch_start = ?")
            vals.append(branch_start)
        if cancelled is not None:
            sets.append("cancelled = ?")
            vals.append(cancelled)
        if clear_assigned:
            sets.append("assigned_to = ''")
        elif assigned_to is not None:
            sets.append("assigned_to = ?")
            vals.append(assigned_to)
        if not sets:
            return self.get(task_id)
        sets.append("updated_at = ?")
        vals.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        vals.extend([task_id, self.wid])
        self._db._conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self._db._conn.commit()
        return self.get(task_id)

    def release(self, task_id: int, freedby: str = "") -> Optional[Dict[str, Any]]:
        """Libère un token après échec d'un agent : repasse en todo, vide
        l'assignation, et pose `freedby` (le dernier agent qui a échoué) pour
        permettre la rotation (l'ordonnanceur peut exclure/pénaliser cet agent,
        et un autre membre reprend le token)."""
        self._db._conn.execute(
            "UPDATE tasks SET status = 'todo', assigned_to = '', "
            "freedby = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ?",
            (freedby, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), task_id, self.wid))
        self._db._conn.commit()
        return self.get(task_id)

    def add_attachment(self, task_id: int, role: str = "work",
                       content: str = "", kind: str = "text",
                       path: str = "") -> int:
        """Attache une annexe de tâche hors git (task_attachments).

        kind='text' : infos textuelles (rapports d'analyse, exploration…) —
        besoin d'un content non vide ; kind='file' : fichier attaché (path),
        unique par (task_id, path) → INSERT OR IGNORE (idempotent)."""
        if kind == "file":
            cur = self._db._conn.execute(
                "INSERT OR IGNORE INTO task_attachments "
                "(task_id, kind, role, content, path) VALUES (?, ?, ?, ?, ?)",
                (task_id, kind, role, content, path))
            self._db._conn.commit()
            return cur.lastrowid
        if not content:
            return 0
        cur = self._db._conn.execute(
            "INSERT INTO task_attachments "
            "(task_id, kind, role, content, path) VALUES (?, ?, ?, ?, ?)",
            (task_id, kind, role, content, path))
        self._db._conn.commit()
        return cur.lastrowid

    def get_attachments(self, task_id: int, roles: Optional[list] = None,
                        kind: Optional[str] = None):
        """Annexes d'une tâche (tous, ou filtrées par rôles / kind)."""
        sql, vals = "SELECT * FROM task_attachments WHERE task_id = ?", [task_id]
        if roles:
            ph = ",".join("?" for _ in roles)
            sql += f" AND role IN ({ph})"
            vals.extend(roles)
        if kind:
            sql += " AND kind = ?"
            vals.append(kind)
        return _rows(self._db._conn.execute(
            sql + " ORDER BY id", tuple(vals)).fetchall())


class SubTaskRepository:
    """Sub_tasks : le RELAIS d'une tâche (une ligne par étape du pipeline).

    États : waiting_dependencies → unattributed → doing → done/cancelled →
    supervised. Le tag est posé par l'agent d'exécution (contraint par type) ;
    le supervisor lève waiting_dependencies → unattributed quand les
    dépendances sont satisfaites, et passe supervised quand le groupe complet
    est clos.
    """

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def create(self, task_id: int, sub_task_type: str,
               difficulty: str = "medium", status: str = "unattributed",
               tag: str = "", repo: str = "", branch: str = "",
               team_id: int = -1, assigned_to: str = "",
               description: str = "", priority: int = 0) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        cur = self._db._conn.execute("""
            INSERT INTO sub_tasks (workspace_id, task_id, team_id, sub_task_type,
                                   status, tag, difficulty, description, assigned_to,
                                   repo, branch, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, task_id, team_id, sub_task_type, status, tag,
              difficulty, description, assigned_to, repo, branch, priority,
              now, now))
        self._db._conn.commit()
        return self.get(cur.lastrowid)

    def get(self, sub_task_id: int) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM sub_tasks WHERE sub_task_id = ? AND workspace_id = ?",
            (sub_task_id, self.wid)).fetchone())

    def list_for_task(self, task_id: int) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM sub_tasks WHERE task_id = ? AND workspace_id = ? "
            "ORDER BY sub_task_id", (task_id, self.wid)).fetchall())

    def list_by_team_status(self, team_id: int, statuses: List[str],
                            limit: int = 50) -> List[Dict[str, Any]]:
        """Sub_tasks d'une team dans un ensemble de statuts (non supervisées) —
        requête ciblée indexée (le ticker ne scanne jamais toute la table).
        Exclut les sub_tasks dont la TÂCHE est cancelled/supervisée/done (les
        résidus ne remplissent pas le LIMIT avant les sub_tasks actives)."""
        ph = ",".join("?" for _ in statuses)
        args = tuple([team_id] + statuses + [limit])
        return _rows(self._db._conn.execute(
            f"SELECT s.* FROM sub_tasks s "
            f"JOIN tasks t ON t.task_id = s.task_id "
            f"WHERE s.team_id = ? AND s.status IN ({ph}) "
            f"AND s.supervised = 0 "
            f"AND COALESCE(t.cancelled, 0) = 0 "
            f"AND t.status NOT IN ('supervised', 'done', 'cancelled') "
            f"ORDER BY s.sub_task_id LIMIT ?", args).fetchall())

    def list_assigned_to(self, agent_name: str) -> List[Dict[str, Any]]:
        """Sub_tasks déjà attribuées à un agent — le greedy les reprend en
        priorité (faux départ / relaunch / attribution directe du supervisor)."""
        return _rows(self._db._conn.execute(
            "SELECT * FROM sub_tasks WHERE assigned_to = ? AND workspace_id = ? "
            "AND status = 'doing' ORDER BY updated_at", (agent_name, self.wid)).fetchall())

    def pick_for(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """PREND la sub_task `attributed` assignée à l'agent, de plus haute
        priorité : attributed → doing. Retourne la sub_task ou None si aucune.

        Parcours basique remplace : on prend la 1re `attributed` à soi, puis on
        change de pick si une suivante a une priorité STRICTEMENT supérieure.
        """
        rows = _rows(self._db._conn.execute(
            "SELECT * FROM sub_tasks WHERE assigned_to = ? AND workspace_id = ? "
            "AND status = 'attributed' AND supervised = 0 "
            "ORDER BY priority DESC, sub_task_id ASC",
            (agent_name, self.wid)).fetchall())
        if not rows:
            return None
        best = rows[0]
        if self.pick(best["sub_task_id"], agent_name):
            return self.get(best["sub_task_id"])
        return None

    def list_by_type(self, sub_task_type: str, status: str = "unattributed",
                     limit: int = 20) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM sub_tasks WHERE sub_task_type = ? AND status = ? "
            "AND workspace_id = ? AND supervised = 0 "
            "ORDER BY sub_task_id LIMIT ?",
            (sub_task_type, status, self.wid, limit)).fetchall())

    def update(self, sub_task_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        sets, vals = [], []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get(sub_task_id)
        sets.append("updated_at = ?")
        vals.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        vals.extend([sub_task_id, self.wid])
        self._db._conn.execute(
            f"UPDATE sub_tasks SET {', '.join(sets)} "
            "WHERE sub_task_id = ? AND workspace_id = ?", vals)
        self._db._conn.commit()
        return self.get(sub_task_id)

    def set_status(self, sub_task_id: int, status: str,
                   assigned_to: str = "", tag: str = "",
                   commit_hash: str = "") -> Optional[Dict[str, Any]]:
        """Transition d'état d'une sub_task (une seule fois, linéaire)."""
        kwargs = {"status": status}
        if assigned_to:
            kwargs["assigned_to"] = assigned_to
        if tag:
            kwargs["tag"] = tag
        if commit_hash:
            kwargs["commit_hash"] = commit_hash
        return self.update(sub_task_id, **kwargs)

    def assign(self, sub_task_id: int, agent_name: str) -> bool:
        """Attribution par le supervisor : unattributed → attributed.

        Le supervisor CHOISIT l'agent : la sub_task passe `attributed` (avec
        assigned_to). Ce n'est que quand l'agent réveillé la PREND réellement
        (pick) qu'elle passe à `doing`. Le flux complet :
        unattributed → attributed → doing → done → supervised."""
        cur = self._db._conn.execute(
            "UPDATE sub_tasks SET status = 'attributed', assigned_to = ?, updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'unattributed'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid))
        self._db._conn.commit()
        return cur.rowcount > 0

    def pick(self, sub_task_id: int, agent_name: str) -> bool:
        """L'agent réveillé PREND une sub_task `attributed` qui lui est
        assignée : attributed → doing (le travail commence réellement)."""
        cur = self._db._conn.execute(
            "UPDATE sub_tasks SET status = 'doing', updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'attributed' "
            "AND assigned_to = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid, agent_name))
        self._db._conn.commit()
        return cur.rowcount > 0

    def release(self, sub_task_id: int, freedby: str = "",
                tag: str = "") -> Optional[Dict[str, Any]]:
        """Libère une sub_task (échec agent) : unattributed, freedby, tag."""
        self._db._conn.execute(
            "UPDATE sub_tasks SET status = 'unattributed', assigned_to = '', "
            "freedby = ?, tag = ?, updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ?",
            (freedby, tag, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid))
        self._db._conn.commit()
        return self.get(sub_task_id)

    def mark_supervised(self, sub_task_id: int) -> Optional[Dict[str, Any]]:
        """Supervised : groupe complet clos par le supervisor (terminal)."""
        return self.update(sub_task_id, supervised=1, status="supervised")

    def mark_too_hard(self, sub_task_id: int, freedby: str = "",
                      reason: str = "") -> Optional[Dict[str, Any]]:
        """L'agent ABANDONNE (doing → too_hard) : la sub_task est trop difficile
        pour lui. Incrémente too_hard_count (le supervisor lira ce compteur pour
        décider : bump_difficulty + re-attribution, ou re-découpe si on a déjà
        trop tenté)."""
        try:
            self._db._conn.execute(
                "UPDATE sub_tasks SET status = 'too_hard', freedby = ?, "
                "too_hard_count = COALESCE(too_hard_count, 0) + 1, "
                "too_hard_reason = ?, assigned_to = '', updated_at = ? "
                "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'doing'",
                (freedby, (reason or "")[:500],
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 sub_task_id, self.wid))
            self._db._conn.commit()
        except Exception:
            return None
        return self.get(sub_task_id)

    def waiting_dependencies(self, sub_task_id: int) -> Optional[Dict[str, Any]]:
        """Passe une sub_task en waiting_dependencies (avant unattributed)."""
        return self.update(sub_task_id, status="waiting_dependencies")

    def add_dependency(self, child_id: int, parent_id: int,
                       required_state: str = "done",
                       required_tag: str = "") -> bool:
        """Dépendance sub_task enfant → parent (état + tag requis). Idempotent."""
        self._db._conn.execute("""
            INSERT OR IGNORE INTO sub_task_dependencies
                (child_id, parent_id, required_state, required_tag)
            VALUES (?, ?, ?, ?)
        """, (child_id, parent_id, required_state, required_tag))
        self._db._conn.commit()
        return True

    def get_parents(self, sub_task_id: int) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM sub_task_dependencies WHERE child_id = ?",
            (sub_task_id,)).fetchall())

    def get_children(self, sub_task_id: int) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM sub_task_dependencies WHERE parent_id = ?",
            (sub_task_id,)).fetchall())

    def dependencies_satisfied(self, sub_task_id: int) -> bool:
        """Toutes les dépendances du noeud sont satisfaites (état + tag).

        Un parent en 'supervised' (traité par le supervisor après done/
        cancelled) reste un livrable valide → satisfait 'done'."""
        deps = self.get_parents(sub_task_id)
        if not deps:
            return True
        for dep in deps:
            p = _row(self._db._conn.execute(
                "SELECT * FROM sub_tasks WHERE sub_task_id = ?",
                (dep["parent_id"],)).fetchone())
            if not p:
                continue
            req = dep.get("required_state", "done")
            ok_state = p["status"] == req or (p["status"] == "supervised"
                                              and req == "done")
            ok_tag = (not dep.get("required_tag")
                      or p.get("tag") == dep.get("required_tag"))
            if not (ok_state and ok_tag):
                return False
        return True


class AskNewTaskRepository:
    """File d'attribution des greedy (remplace sleep + pick).

    L'agent écrit sa demande (agent_id + types), le supervisor répond en
    synchrone. La ligne sert de traçabilité et de file de secours pour le
    tick failsafe.
    """

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def create(self, agent_id: int, types: list) -> int:
        import json as _json
        cur = self._db._conn.execute(
            "INSERT INTO ask_new_task (workspace_id, agent_id, types) "
            "VALUES (?, ?, ?)",
            (self.wid, agent_id, _json.dumps(types or [])))
        self._db._conn.commit()
        return cur.lastrowid

    def list_pending(self, agent_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if agent_id is None:
            return _rows(self._db._conn.execute(
                "SELECT * FROM ask_new_task WHERE status = 'pending' "
                "AND workspace_id = ? ORDER BY requested_at", (self.wid,)).fetchall())
        return _rows(self._db._conn.execute(
            "SELECT * FROM ask_new_task WHERE status = 'pending' "
            "AND workspace_id = ? AND agent_id = ? ORDER BY requested_at",
            (self.wid, agent_id)).fetchall())

    def serve(self, ask_id: int, sub_task_id: int) -> None:
        self._db._conn.execute(
            "UPDATE ask_new_task SET status = 'served', served_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), ask_id))
        self._db._conn.commit()

    def mark_wait(self, ask_id: int) -> None:
        self._db._conn.execute(
            "UPDATE ask_new_task SET status = 'answered_wait' WHERE id = ?",
            (ask_id,))
        self._db._conn.commit()


class SupervisorRulesRepository:
    """Tableau de règles du task_supervisor (par team/workspace).

    Règle : (in_type, in_tag) → (out_type, out_tag). Généraliste par défaut
    (team_id=-1, workspace_id=''), surchargeable par team. out_type='' →
    pas de création (le noeud passe supervised)."""

    def __init__(self, db: Db, workspace_id: str = ""):
        self._db = db
        self.wid = workspace_id

    def add_rule(self, in_type: str, in_tag: str, out_type: str,
                 out_tag: str = "", team_id: int = -1,
                 workspace_id: str = "", priority: int = 0) -> int:
        cur = self._db._conn.execute("""
            INSERT INTO task_supervisor_rules
                (workspace_id, team_id, in_type, in_tag, out_type, out_tag, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id or self.wid, team_id, in_type, in_tag,
              out_type, out_tag, priority))
        self._db._conn.commit()
        return cur.lastrowid

    def list_rules(self, workspace_id: str = "", team_id: int = -1
                   ) -> List[Dict[str, Any]]:
        """Règles applicables : les plus spécifiques d'abord (workspace+team,
        puis workspace, puis globales)."""
        return _rows(self._db._conn.execute("""
            SELECT * FROM task_supervisor_rules
            WHERE enabled = 1
              AND ((workspace_id = ? AND team_id = ?)
                OR (workspace_id = ? AND team_id = -1)
                OR (workspace_id = '' AND team_id = -1))
            ORDER BY priority DESC, rule_id
        """, (workspace_id or self.wid, team_id, workspace_id or self.wid)).fetchall())

    def find_rule(self, in_type: str, in_tag: str,
                  workspace_id: str = "", team_id: int = -1
                  ) -> Optional[Dict[str, Any]]:
        """Règle la plus spécifique qui matche (in_type, in_tag)."""
        for r in self.list_rules(workspace_id, team_id):
            it = r.get("in_type", "")
            if it and it != in_type:
                continue
            ig = r.get("in_tag", "")
            if ig and ig != in_tag:
                continue
            return r
        return None


class ConsensusRepository:
    """Consensus : question + réponses des answering_machine.

    L'agent consensus pose une question (status_answering awaiting) ; les
    answering_machine répondent (table reponse) ; le maître juge (vote
    majorité / élimination / escalade / hasard). Voir carnet-d-idees.md."""

    def __init__(self, db: Db, workspace_id: str = ""):
        self._db = db
        self.wid = workspace_id

    def create_question(self, question: str, id_creator: int = 0,
                        options: list = None, max_tours: int = 5) -> Dict[str, Any]:
        cur = self._db._conn.execute(
            "INSERT INTO question (workspace_id, question, id_creator, "
            "options_json, max_tours, status_answering, tour_courant) "
            "VALUES (?, ?, ?, ?, ?, 'awaiting', 1)",
            (self.wid, question, id_creator or None,
             json.dumps(options or []), max_tours))
        self._db._conn.commit()
        return {"id_question": cur.lastrowid, "question": question,
                "status_answering": "awaiting", "tour_courant": 1,
                "options": options or []}

    def get_question(self, id_question: int) -> Optional[Dict[str, Any]]:
        row = self._db._conn.execute(
            "SELECT * FROM question WHERE id_question = ?",
            (id_question,)).fetchone()
        if not row:
            return None
        return dict(row)

    def list_questions(self, status: str = "") -> List[Dict[str, Any]]:
        q = ("SELECT * FROM question WHERE workspace_id = ?"
             + (" AND status_answering = ?" if status else ""))
        args = (self.wid,) + ((status,) if status else ())
        return [dict(r) for r in self._db._conn.execute(q, args).fetchall()]

    def set_status(self, id_question: int, status: str) -> None:
        self._db._conn.execute(
            "UPDATE question SET status_answering = ?, "
            "answered_at = datetime('now') WHERE id_question = ?",
            (status, id_question))
        self._db._conn.commit()

    def add_reponse(self, id_question: int, id_agent: int,
                    contenu: str, model_ref: str = "") -> Dict[str, Any]:
        cur = self._db._conn.execute(
            "INSERT INTO reponse (id_question, id_agent, model_ref, contenu) "
            "VALUES (?, ?, ?, ?)",
            (id_question, id_agent, model_ref, contenu))
        self._db._conn.commit()
        return {"id_reponse": cur.lastrowid, "id_question": id_question,
                "id_agent": id_agent, "contenu": contenu}

    def get_reponses(self, id_question: int) -> List[Dict[str, Any]]:
        return [dict(r) for r in self._db._conn.execute(
            "SELECT * FROM reponse WHERE id_question = ? ORDER BY id_reponse",
            (id_question,)).fetchall()]

    def set_jugement(self, id_reponse: int, jugement: str) -> None:
        self._db._conn.execute(
            "UPDATE reponse SET jugement = ? WHERE id_reponse = ?",
            (jugement, id_reponse))
        self._db._conn.commit()


class IssueRepository:
    """Issues d'un workspace (demandes de haut niveau → découpées en tâches)."""

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def list_open(self) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? AND status IN ('open','analysing','analyzed') "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? ORDER BY priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, issue_id: int) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM issues WHERE issue_id = ? AND workspace_id = ?",
            (issue_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0, parent_id: int = None,
               team_id: int = -1) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        cur = self._db._conn.execute("""
            INSERT INTO issues (workspace_id, title, description, priority,
                                parent_id, team_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority, parent_id, team_id, now, now))
        self._db._conn.commit()
        return self.get(cur.lastrowid)

    def update(self, issue_id: int, **kwargs) -> Optional[Dict[str, Any]]:
        sets = []
        vals = []
        for k, v in kwargs.items():
            sets.append(f"{k} = ?")
            vals.append(v)
        if not sets:
            return self.get(issue_id)
        sets.append("updated_at = ?")
        vals.append(datetime.now(timezone.utc).replace(tzinfo=None).isoformat())
        vals.extend([issue_id, self.wid])
        self._db._conn.execute(
            f"UPDATE issues SET {', '.join(sets)} "
            "WHERE issue_id = ? AND workspace_id = ?", vals)
        self._db._conn.commit()
        return self.get(issue_id)

    def claim(self, issue_id: int, agent_name: str) -> bool:
        cur = self._db._conn.execute(
            "UPDATE issues SET status = 'analysing', assigned_to = ?, updated_at = ? "
            "WHERE issue_id = ? AND workspace_id = ? AND status = 'open'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), issue_id, self.wid))
        self._db._conn.commit()
        return cur.rowcount > 0


class ChatroomRepository:
    """Messages de discussion d'un workspace."""

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def post(self, sender_agent_id: int, content: str,
             msg_type: str = "text", parent_id: int = None) -> Dict[str, Any]:
        cur = self._db._conn.execute("""
            INSERT INTO chatroom_messages
                (workspace_id, sender_agent_id, msg_type, content, parent_id)
            VALUES (?, ?, ?, ?, ?)
        """, (self.wid, sender_agent_id, msg_type, content, parent_id))
        self._db._conn.commit()
        return self.get(cur.lastrowid)

    def get(self, msg_id: int) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM chatroom_messages WHERE id = ? AND workspace_id = ?",
            (msg_id, self.wid)).fetchone())

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute(
            "SELECT * FROM chatroom_messages WHERE workspace_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (self.wid, limit)).fetchall())

    def thread(self, root_id: int) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute("""
            WITH RECURSIVE tree AS (
                SELECT * FROM chatroom_messages WHERE id = ?
                UNION ALL
                SELECT m.* FROM chatroom_messages m
                JOIN tree ON m.parent_id = tree.id
            )
            SELECT * FROM tree WHERE workspace_id = ? ORDER BY created_at
        """, (root_id, self.wid)).fetchall())


class UsageFileRepository:
    """Tracking d'accès aux fichiers d'un workspace."""

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id

    def touch_read(self, path: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._db._conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_read_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_read_at = ?
        """, (path, self.wid, now, now))
        self._db._conn.commit()

    def touch_write(self, path: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self._db._conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_write_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_write_at = ?
        """, (path, self.wid, now, now))
        self._db._conn.commit()

    def hot(self, limit: int = 10) -> List[Dict[str, Any]]:
        return _rows(self._db._conn.execute("""
            SELECT * FROM usage_files WHERE workspace_id = ?
            ORDER BY access_count DESC LIMIT ?
        """, (self.wid, limit)).fetchall())

    def get(self, path: str) -> Optional[Dict[str, Any]]:
        return _row(self._db._conn.execute(
            "SELECT * FROM usage_files WHERE workspace_id = ? AND path = ?",
            (self.wid, path)).fetchone())


# ── DB ────────────────────────────────────────────


## DB

## SCOPE

class WorkspaceDB:
    """Point d'entrée pour workspace.db (domaine sqlite workspace).

    Branchement sur le domaine modules.sqlite.workspace : le schéma est
    appliqué par le domaine (db()), chaque repos reçoit le Db. Autocommit
    + WAL gérés par base.py. L'API est identique au legacy sql_old.

    Usage:
        db = WorkspaceDB()
        w = db.for_workspace("proj42")
        w.tasks.create("Fix bug")
        db.close()
    """

    def __init__(self, db_: Optional[Db] = None):
        self._db = db_ or workspace_db()
        self.workspaces = WorkspaceRepository(self._db)
        self.config = WorkspaceConfigRepository(self._db)

    @property
    def conn(self):
        """Compat legacy : connexion brute du domaine (lectures + écritures
        directes des services non encore migrés, ex. watcher)."""
        return self._db._conn

    def for_workspace(self, workspace_id: str) -> "WorkspaceScope":
        return WorkspaceScope(self._db, workspace_id)

    def close(self):
        self._db.close()

class WorkspaceScope:
    """Accès à toutes les données d'un workspace spécifique.

    Créé par WorkspaceDB.for_workspace().
    Les lectures sont paresseuses (SQLite lit page par page).
    """

    def __init__(self, db: Db, workspace_id: str):
        self._db = db
        self.wid = workspace_id
        self.tasks = TaskRepository(db, workspace_id)
        self.issues = IssueRepository(db, workspace_id)
        self.chat = ChatroomRepository(db, workspace_id)
        self.usage = UsageFileRepository(db, workspace_id)
        self.sub_tasks = SubTaskRepository(db, workspace_id)
        self.ask = AskNewTaskRepository(db, workspace_id)
        self.rules = SupervisorRulesRepository(db, workspace_id)
        self.consensus = ConsensusRepository(db, workspace_id)


def chatroom_send_message(team_id=None, agent_id="", msg="",
                          workspace_id="") -> Dict[str, Any]:
    """(pont résolution runtime) Envoie un message à la chatroom.

    workspace_id explicite si fourni ; sinon résolu via les variables de
    l'agent (variables_json.workspace_id, défaut mw-dev-chat). team_id est
    accepté pour compat (le workspace reste la clé de la chatroom)."""
    try:
        wid = workspace_id or ""
        if not wid and agent_id:
            try:
                from modules.sqlite.agent import db as agent_db
                row = agent_db().table("agents").get({"agent_id": int(str(agent_id).split("_")[-1])})
                if row:
                    wid = (json.loads(row["variables_json"] or "{}")
                           .get("workspace_id", ""))
            except Exception:
                pass
        wid = wid or "mw-dev-chat"
        wdb = WorkspaceDB()
        try:
            scope = wdb.for_workspace(wid)
            return scope.chat.post(int(agent_id), str(msg))
        finally:
            wdb.close()
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}
