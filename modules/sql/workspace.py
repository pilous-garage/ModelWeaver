"""Workspace DB — projets, tâches, échanges inter-agents.

Une base unique pour tous les workspaces, chargement paresseux :
les requêtes ne lisent que les pages SQLite concernées.

Usage:
    db = WorkspaceDB()
    for ws in db.workspaces.list():
        print(ws["name"])
    tasks = db.for_workspace("proj42").tasks.list_pending()
    db.close()
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home


def _default_workspace_db() -> Path:
    return mw_home() / "workspace.db"


def _row(row):
    return dict(row) if row else None


def _rows(rows):
    return [dict(r) for r in rows]


# Hiérarchie de capabilité : un niveau peut traiter les tâches de son niveau
# et des niveaux inférieurs (senior > mid > junior). Pour chaque catégorie de
# rôle (coder, tester, reviewer...), on étend le niveau demandé vers le bas.
_LEVEL_RANK = {"junior": 0, "mid": 1, "senior": 2}

# Niveaux de difficulté d'une tâche (croissant). Le niveau de l'agent
# (débutant/junior/intermédiaire/senior) borne la difficulté piochable par
# type : un coder senior peut traiter coding/easy..expert, un reviewer_code
# junior seulement code_review/easy+medium.
_DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2, "expert": 3}


def _migrate_role_required_to_task_type(conn) -> None:
    """V0.10 : bascule vers le modèle token (task_type + dépendances).

    Les données existantes n'ont pas de valeur (phase de dev) → clean de la
    table tasks UNE FOIS au moment du passage au nouveau schéma (quand
    role_required/parent_id sont encore présents). Après, rien n'est effacé.
    """
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    migrated = False
    # Renommer la colonne si elle existe encore et que task_type est absent.
    if "role_required" in cols and "task_type" not in cols:
        try:
            conn.execute("ALTER TABLE tasks RENAME COLUMN role_required TO task_type")
            migrated = True
        except Exception:
            pass
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    # Si role_required coexiste encore avec task_type (migration précédente
    # partielle), on le droppe : task_type est la source de vérité.
    if "role_required" in cols and "task_type" in cols:
        try:
            conn.execute("ALTER TABLE tasks DROP COLUMN role_required")
            migrated = True
        except Exception:
            pass
    # parent_id devient redondant : task_dependencies est la seule source de
    # parenté.
    cols2 = {r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()}
    if "parent_id" in cols2:
        try:
            conn.execute("ALTER TABLE tasks DROP COLUMN parent_id")
            migrated = True
        except Exception:
            pass
    # Clean des données UNIQUEMENT à la migration (rien de valeur à préserver
    # en phase de dev). Ne pas ré-effacer à chaque ouverture.
    if migrated:
        try:
            conn.execute("DELETE FROM task_dependencies")
            conn.execute("DELETE FROM tasks")
            conn.commit()
        except Exception:
            pass


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


# ── Repositories ──────────────────────────────────


class WorkspaceRepository:
    """Méta des workspaces (liste légère, pas de chargement lourd)."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def list(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT workspace_id, name, description, director, "
            "git_shared, created_at, last_activity_at "
            "FROM workspaces ORDER BY last_activity_at DESC"
        ).fetchall())

    def get(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM workspaces WHERE workspace_id = ?",
            (workspace_id,)
        ).fetchone())

    def create(self, workspace_id: str, name: str, description: str = "",
               director: Optional[str] = None, git_shared: str = "") -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self.conn.execute("""
            INSERT INTO workspaces (workspace_id, name, description, director,
                                    git_shared, created_at, last_activity_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id, name, description, director, git_shared, now, now))
        self.conn.commit()
        return self.get(workspace_id)

    def touch(self, workspace_id: str) -> None:
        self.conn.execute(
            "UPDATE workspaces SET last_activity_at = ? WHERE workspace_id = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), workspace_id))
        self.conn.commit()

    def delete(self, workspace_id: str) -> None:
        self.conn.execute("DELETE FROM workspaces WHERE workspace_id = ?",
                          (workspace_id,))
        self.conn.commit()


class WorkspaceConfigRepository:
    """Configuration clef/valeur par workspace."""

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def get(self, workspace_id: str, key: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT value FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key)).fetchone()
        return row[0] if row else None

    def set(self, workspace_id: str, key: str, value: str) -> None:
        self.conn.execute("""
            INSERT OR REPLACE INTO workspace_config (workspace_id, key, value)
            VALUES (?, ?, ?)
        """, (workspace_id, key, value))
        self.conn.commit()

    def delete(self, workspace_id: str, key: str) -> None:
        self.conn.execute(
            "DELETE FROM workspace_config WHERE workspace_id = ? AND key = ?",
            (workspace_id, key))
        self.conn.commit()

    def all(self, workspace_id: str) -> Dict[str, str]:
        rows = self.conn.execute(
            "SELECT key, value FROM workspace_config WHERE workspace_id = ?",
            (workspace_id,)).fetchall()
        return {r["key"]: r["value"] for r in rows}


class TaskRepository:
    """Tâches d'un workspace."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def list_pending(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? AND status = 'pending' "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM tasks WHERE workspace_id = ? "
            "ORDER BY status, priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, task_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM tasks WHERE task_id = ? AND workspace_id = ?",
            (task_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0,
               difficulty: str = "medium", task_type: str = "",
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
        cur = self.conn.execute("""
            INSERT INTO tasks (workspace_id, title, description, priority,
                               status, difficulty, task_type, team_id,
                               repo, branch, base_commit,
                               commit_start, branch_start, primordial,
                               deadline, estimated_minutes,
                               created_at, updated_at)
            VALUES (?, ?, ?, ?, 'todo', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority,
              difficulty, task_type, team_id, repo, branch, base_commit,
              cs, bs, primordial, deadline, estimated_minutes,
              created, now))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def add_dependency(self, child_id: int, parent_id: int,
                       required_state: str = "done") -> bool:
        """Lie un enfant à un parent : l'enfant n'est piochable que si le
        parent est dans l'état requis (dépendance totale). Idempotent."""
        self.conn.execute("""
            INSERT OR IGNORE INTO task_dependencies
                (task_id, parent_id, required_state)
            VALUES (?, ?, ?)
        """, (child_id, parent_id, required_state))
        self.conn.commit()
        return True

    def get_parents(self, task_id: int) -> List[Dict[str, Any]]:
        """Parents de la tâche (dont elle dépend) avec leur état requis."""
        return _rows(self.conn.execute(
            "SELECT d.parent_id, d.required_state, p.status, p.task_type, p.title "
            "FROM task_dependencies d JOIN tasks p ON p.task_id = d.parent_id "
            "WHERE d.task_id = ?", (task_id,)).fetchall())

    def get_children(self, task_id: int) -> List[Dict[str, Any]]:
        """Enfants de la tâche (ceux qui dépendent d'elle) — la filiation."""
        return _rows(self.conn.execute(
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
        rows = self.conn.execute(sql, args).fetchall()
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
        row = self.conn.execute(
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
        row = _row(self.conn.execute(sel, sel_args).fetchone())
        if not row:
            return None
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'doing', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'todo'",
            (assigned_to, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             row["task_id"], self.wid))
        self.conn.commit()
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
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(task_id)

    def claim(self, task_id: int, agent_name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE tasks SET status = 'running', assigned_to = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ? AND status = 'pending'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), task_id, self.wid))
        self.conn.commit()
        return cur.rowcount > 0

    def done(self, task_id: int, branch: str = "",
             commit_hash: str = "") -> Optional[Dict[str, Any]]:
        self.conn.execute("""
            UPDATE tasks SET status = 'done', branch = ?, commit_hash = ?,
                            updated_at = ?
            WHERE task_id = ? AND workspace_id = ?
        """, (branch, commit_hash, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
              task_id, self.wid))
        self.conn.commit()
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
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
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
        self.conn.execute(
            f"UPDATE tasks SET {', '.join(sets)} "
            "WHERE task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(task_id)

    def release(self, task_id: int, freedby: str = "") -> Optional[Dict[str, Any]]:
        """Libère un token après échec d'un agent : repasse en todo, vide
        l'assignation, et pose `freedby` (le dernier agent qui a échoué) pour
        permettre la rotation (l'ordonnanceur peut exclure/pénaliser cet agent,
        et un autre membre reprend le token)."""
        self.conn.execute(
            "UPDATE tasks SET status = 'todo', assigned_to = '', "
            "freedby = ?, updated_at = ? "
            "WHERE task_id = ? AND workspace_id = ?",
            (freedby, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), task_id, self.wid))
        self.conn.commit()
        return self.get(task_id)

    def add_file(self, task_id: int, path: str, role: str = "source") -> None:
        self.conn.execute(
            "INSERT OR IGNORE INTO task_files (task_id, path, role) VALUES (?, ?, ?)",
            (task_id, path, role))
        self.conn.commit()

    def get_files(self, task_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM task_files WHERE task_id = ?", (task_id,)).fetchall())

    # ── Rapports d'analyse (task_reports) ──

    def add_report(self, task_id: int, role: str = "analysis",
                   content: str = "") -> int:
        """Attache un rapport d'analyse à une tâche (transmis à la chaîne)."""
        if not content:
            return 0
        cur = self.conn.execute(
            "INSERT INTO task_reports (task_id, role, content) VALUES (?, ?, ?)",
            (task_id, role, content))
        self.conn.commit()
        return cur.lastrowid

    def get_reports(self, task_id: int, roles: Optional[list] = None):
        """Rapports d'une tâche (tous, ou filtrés par rôles)."""
        if roles:
            ph = ",".join("?" for _ in roles)
            return _rows(self.conn.execute(
                f"SELECT * FROM task_reports WHERE task_id = ? "
                f"AND role IN ({ph}) ORDER BY id", (task_id, *roles)).fetchall())
        return _rows(self.conn.execute(
            "SELECT * FROM task_reports WHERE task_id = ? ORDER BY id",
            (task_id,)).fetchall())


class SubTaskRepository:
    """Sub_tasks : le RELAIS d'une tâche (une ligne par étape du pipeline).

    États : waiting_dependencies → unattributed → doing → done/cancelled →
    supervised. Le tag est posé par l'agent d'exécution (contraint par type) ;
    le supervisor lève waiting_dependencies → unattributed quand les
    dépendances sont satisfaites, et passe supervised quand le groupe complet
    est clos.
    """

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def create(self, task_id: int, sub_task_type: str,
               difficulty: str = "medium", status: str = "unattributed",
               tag: str = "", repo: str = "", branch: str = "",
               team_id: int = -1, assigned_to: str = "",
               description: str = "", priority: int = 0) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        cur = self.conn.execute("""
            INSERT INTO sub_tasks (workspace_id, task_id, team_id, sub_task_type,
                                   status, tag, difficulty, description, assigned_to,
                                   repo, branch, priority, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, task_id, team_id, sub_task_type, status, tag,
              difficulty, description, assigned_to, repo, branch, priority,
              now, now))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, sub_task_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM sub_tasks WHERE sub_task_id = ? AND workspace_id = ?",
            (sub_task_id, self.wid)).fetchone())

    def list_for_task(self, task_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM sub_tasks WHERE task_id = ? AND workspace_id = ? "
            "ORDER BY sub_task_id", (task_id, self.wid)).fetchall())

    def list_by_team_status(self, team_id: int, statuses: List[str],
                            limit: int = 50) -> List[Dict[str, Any]]:
        """Sub_tasks d'une team dans un ensemble de statuts (non supervisées) —
        requête ciblée indexée (le ticker ne scanne jamais toute la table)."""
        ph = ",".join("?" for _ in statuses)
        args = tuple([team_id] + statuses + [limit])
        return _rows(self.conn.execute(
            f"SELECT * FROM sub_tasks WHERE team_id = ? AND status IN ({ph}) "
            f"AND supervised = 0 ORDER BY sub_task_id LIMIT ?", args).fetchall())

    def list_assigned_to(self, agent_name: str) -> List[Dict[str, Any]]:
        """Sub_tasks déjà attribuées à un agent — le greedy les reprend en
        priorité (faux départ / relaunch / attribution directe du supervisor)."""
        return _rows(self.conn.execute(
            "SELECT * FROM sub_tasks WHERE assigned_to = ? AND workspace_id = ? "
            "AND status = 'doing' ORDER BY updated_at", (agent_name, self.wid)).fetchall())

    def pick_for(self, agent_name: str) -> Optional[Dict[str, Any]]:
        """PREND la sub_task `attributed` assignée à l'agent, de plus haute
        priorité : attributed → doing. Retourne la sub_task ou None si aucune.

        Parcours basique remplace : on prend la 1re `attributed` à soi, puis on
        change de pick si une suivante a une priorité STRICTEMENT supérieure.
        """
        rows = _rows(self.conn.execute(
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
        return _rows(self.conn.execute(
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
        self.conn.execute(
            f"UPDATE sub_tasks SET {', '.join(sets)} "
            "WHERE sub_task_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
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
        cur = self.conn.execute(
            "UPDATE sub_tasks SET status = 'attributed', assigned_to = ?, updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'unattributed'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid))
        self.conn.commit()
        return cur.rowcount > 0

    def pick(self, sub_task_id: int, agent_name: str) -> bool:
        """L'agent réveillé PREND une sub_task `attributed` qui lui est
        assignée : attributed → doing (le travail commence réellement)."""
        cur = self.conn.execute(
            "UPDATE sub_tasks SET status = 'doing', updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'attributed' "
            "AND assigned_to = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid, agent_name))
        self.conn.commit()
        return cur.rowcount > 0

    def release(self, sub_task_id: int, freedby: str = "",
                tag: str = "") -> Optional[Dict[str, Any]]:
        """Libère une sub_task (échec agent) : unattributed, freedby, tag."""
        self.conn.execute(
            "UPDATE sub_tasks SET status = 'unattributed', assigned_to = '', "
            "freedby = ?, tag = ?, updated_at = ? "
            "WHERE sub_task_id = ? AND workspace_id = ?",
            (freedby, tag, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
             sub_task_id, self.wid))
        self.conn.commit()
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
            self.conn.execute(
                "UPDATE sub_tasks SET status = 'too_hard', freedby = ?, "
                "too_hard_count = COALESCE(too_hard_count, 0) + 1, "
                "too_hard_reason = ?, assigned_to = '', updated_at = ? "
                "WHERE sub_task_id = ? AND workspace_id = ? AND status = 'doing'",
                (freedby, (reason or "")[:500],
                 datetime.now(timezone.utc).replace(tzinfo=None).isoformat(),
                 sub_task_id, self.wid))
            self.conn.commit()
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
        self.conn.execute("""
            INSERT OR IGNORE INTO sub_task_dependencies
                (child_id, parent_id, required_state, required_tag)
            VALUES (?, ?, ?, ?)
        """, (child_id, parent_id, required_state, required_tag))
        self.conn.commit()
        return True

    def get_parents(self, sub_task_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM sub_task_dependencies WHERE child_id = ?",
            (sub_task_id,)).fetchall())

    def get_children(self, sub_task_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
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
            p = _row(self.conn.execute(
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

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def create(self, agent_id: int, types: list) -> int:
        import json as _json
        cur = self.conn.execute(
            "INSERT INTO ask_new_task (workspace_id, agent_id, types) "
            "VALUES (?, ?, ?)",
            (self.wid, agent_id, _json.dumps(types or [])))
        self.conn.commit()
        return cur.lastrowid

    def list_pending(self, agent_id: Optional[int] = None) -> List[Dict[str, Any]]:
        if agent_id is None:
            return _rows(self.conn.execute(
                "SELECT * FROM ask_new_task WHERE status = 'pending' "
                "AND workspace_id = ? ORDER BY requested_at", (self.wid,)).fetchall())
        return _rows(self.conn.execute(
            "SELECT * FROM ask_new_task WHERE status = 'pending' "
            "AND workspace_id = ? AND agent_id = ? ORDER BY requested_at",
            (self.wid, agent_id)).fetchall())

    def serve(self, ask_id: int, sub_task_id: int) -> None:
        self.conn.execute(
            "UPDATE ask_new_task SET status = 'served', served_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), ask_id))
        self.conn.commit()

    def mark_wait(self, ask_id: int) -> None:
        self.conn.execute(
            "UPDATE ask_new_task SET status = 'answered_wait' WHERE id = ?",
            (ask_id,))
        self.conn.commit()


class SupervisorRulesRepository:
    """Tableau de règles du task_supervisor (par team/workspace).

    Règle : (in_type, in_tag) → (out_type, out_tag). Généraliste par défaut
    (team_id=-1, workspace_id=''), surchargeable par team. out_type='' →
    pas de création (le noeud passe supervised)."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str = ""):
        self.conn = conn
        self.wid = workspace_id

    def add_rule(self, in_type: str, in_tag: str, out_type: str,
                 out_tag: str = "", team_id: int = -1,
                 workspace_id: str = "", priority: int = 0) -> int:
        cur = self.conn.execute("""
            INSERT INTO task_supervisor_rules
                (workspace_id, team_id, in_type, in_tag, out_type, out_tag, priority)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (workspace_id or self.wid, team_id, in_type, in_tag,
              out_type, out_tag, priority))
        self.conn.commit()
        return cur.lastrowid

    def list_rules(self, workspace_id: str = "", team_id: int = -1
                   ) -> List[Dict[str, Any]]:
        """Règles applicables : les plus spécifiques d'abord (workspace+team,
        puis workspace, puis globales)."""
        return _rows(self.conn.execute("""
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

    def __init__(self, conn: sqlite3.Connection, workspace_id: str = ""):
        self.conn = conn
        self.wid = workspace_id

    def create_question(self, question: str, id_creator: int = 0,
                        options: list = None, max_tours: int = 5) -> Dict[str, Any]:
        cur = self.conn.execute(
            "INSERT INTO question (workspace_id, question, id_creator, "
            "options_json, max_tours, status_answering, tour_courant) "
            "VALUES (?, ?, ?, ?, ?, 'awaiting', 1)",
            (self.wid, question, id_creator or None,
             json.dumps(options or []), max_tours))
        self.conn.commit()
        return {"id_question": cur.lastrowid, "question": question,
                "status_answering": "awaiting", "tour_courant": 1,
                "options": options or []}

    def get_question(self, id_question: int) -> Optional[Dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM question WHERE id_question = ?",
            (id_question,)).fetchone()
        if not row:
            return None
        return dict(row)

    def list_questions(self, status: str = "") -> List[Dict[str, Any]]:
        q = ("SELECT * FROM question WHERE workspace_id = ?"
             + (" AND status_answering = ?" if status else ""))
        args = (self.wid,) + ((status,) if status else ())
        return [dict(r) for r in self.conn.execute(q, args).fetchall()]

    def set_status(self, id_question: int, status: str) -> None:
        self.conn.execute(
            "UPDATE question SET status_answering = ?, "
            "answered_at = datetime('now') WHERE id_question = ?",
            (status, id_question))
        self.conn.commit()

    def add_reponse(self, id_question: int, id_agent: int,
                    contenu: str, model_ref: str = "") -> Dict[str, Any]:
        cur = self.conn.execute(
            "INSERT INTO reponse (id_question, id_agent, model_ref, contenu) "
            "VALUES (?, ?, ?, ?)",
            (id_question, id_agent, model_ref, contenu))
        self.conn.commit()
        return {"id_reponse": cur.lastrowid, "id_question": id_question,
                "id_agent": id_agent, "contenu": contenu}

    def get_reponses(self, id_question: int) -> List[Dict[str, Any]]:
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM reponse WHERE id_question = ? ORDER BY id_reponse",
            (id_question,)).fetchall()]

    def set_jugement(self, id_reponse: int, jugement: str) -> None:
        self.conn.execute(
            "UPDATE reponse SET jugement = ? WHERE id_reponse = ?",
            (jugement, id_reponse))
        self.conn.commit()


class IssueRepository:
    """Issues d'un workspace (demandes de haut niveau → découpées en tâches)."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def list_open(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? AND status IN ('open','analysing','analyzed') "
            "ORDER BY priority DESC, created_at", (self.wid,)).fetchall())

    def list_all(self) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM issues WHERE workspace_id = ? ORDER BY priority DESC, created_at",
            (self.wid,)).fetchall())

    def get(self, issue_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM issues WHERE issue_id = ? AND workspace_id = ?",
            (issue_id, self.wid)).fetchone())

    def create(self, title: str, description: str = "",
               priority: int = 0, parent_id: int = None,
               team_id: int = -1) -> Dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        cur = self.conn.execute("""
            INSERT INTO issues (workspace_id, title, description, priority,
                                parent_id, team_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (self.wid, title, description, priority, parent_id, team_id, now, now))
        self.conn.commit()
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
        self.conn.execute(
            f"UPDATE issues SET {', '.join(sets)} "
            "WHERE issue_id = ? AND workspace_id = ?", vals)
        self.conn.commit()
        return self.get(issue_id)

    def claim(self, issue_id: int, agent_name: str) -> bool:
        cur = self.conn.execute(
            "UPDATE issues SET status = 'analysing', assigned_to = ?, updated_at = ? "
            "WHERE issue_id = ? AND workspace_id = ? AND status = 'open'",
            (agent_name, datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), issue_id, self.wid))
        self.conn.commit()
        return cur.rowcount > 0


class ChatroomRepository:
    """Messages de discussion d'un workspace."""

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def post(self, sender_agent_id: int, content: str,
             msg_type: str = "text", parent_id: int = None) -> Dict[str, Any]:
        cur = self.conn.execute("""
            INSERT INTO chatroom_messages
                (workspace_id, sender_agent_id, msg_type, content, parent_id)
            VALUES (?, ?, ?, ?, ?)
        """, (self.wid, sender_agent_id, msg_type, content, parent_id))
        self.conn.commit()
        return self.get(cur.lastrowid)

    def get(self, msg_id: int) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM chatroom_messages WHERE id = ? AND workspace_id = ?",
            (msg_id, self.wid)).fetchone())

    def recent(self, limit: int = 50) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute(
            "SELECT * FROM chatroom_messages WHERE workspace_id = ? "
            "ORDER BY created_at DESC LIMIT ?",
            (self.wid, limit)).fetchall())

    def thread(self, root_id: int) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute("""
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

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id

    def touch_read(self, path: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self.conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_read_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_read_at = ?
        """, (path, self.wid, now, now))
        self.conn.commit()

    def touch_write(self, path: str) -> None:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat()
        self.conn.execute("""
            INSERT INTO usage_files (path, workspace_id, access_count,
                                     last_write_at)
            VALUES (?, ?, 1, ?)
            ON CONFLICT(workspace_id, path) DO UPDATE SET
                access_count = access_count + 1,
                last_write_at = ?
        """, (path, self.wid, now, now))
        self.conn.commit()

    def hot(self, limit: int = 10) -> List[Dict[str, Any]]:
        return _rows(self.conn.execute("""
            SELECT * FROM usage_files WHERE workspace_id = ?
            ORDER BY access_count DESC LIMIT ?
        """, (self.wid, limit)).fetchall())

    def get(self, path: str) -> Optional[Dict[str, Any]]:
        return _row(self.conn.execute(
            "SELECT * FROM usage_files WHERE workspace_id = ? AND path = ?",
            (self.wid, path)).fetchone())


# ── DB ────────────────────────────────────────────


class WorkspaceDB:
    """Point d'entrée pour workspace.db.

    Chargement paresseux : les requêtes ne lisent que les pages
    SQLite nécessaires. Les listes de workspaces sont légères.

    Usage:
        db = WorkspaceDB()
        for ws in db.workspaces.list():
            print(ws["name"])
        w = db.for_workspace("proj42")
        w.tasks.create("Fix bug")
        for msg in w.chat.recent():
            print(msg["content"])
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_workspace_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit : les agents tournent en threads (agent-manager) et le
        # watcher est un process séparé qui partagent cette DB. Sans autocommit,
        # un write non commité laisse une transaction implicite ouverte → lock
        # d'écriture tenu à vie → "database is locked" pour les autres
        # écrivains. Chaque execute est immédiat.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        # (autocommit : plus de transaction implicite à fermer après le DDL)
        self.workspaces = WorkspaceRepository(self.conn)
        self.config = WorkspaceConfigRepository(self.conn)

    def _ensure_schema(self):
        schema = Path(__file__).resolve().parent / "workspace_schema.sql"
        if schema.exists():
            try:
                self.conn.executescript(schema.read_text())
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
        # Migrations : colonnes ajoutées sur des tables déjà existantes.
        try:
            from modules.sql.db import _add_column_if_missing
            _add_column_if_missing(self.conn, "tasks", "difficulty", "TEXT DEFAULT 'medium'")
            _add_column_if_missing(self.conn, "tasks", "team_id", "INTEGER DEFAULT -1")
            # V0.9.x : une tâche pointe sur un repo local + branche (ou commit).
            _add_column_if_missing(self.conn, "tasks", "repo", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "base_commit", "TEXT DEFAULT ''")
            # V0.11 : tracking git du cycle de vie (cancel/clear) + nature.
            _add_column_if_missing(self.conn, "tasks", "commit_start", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "branch_start", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "commit_current", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "branch_current", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "primordial", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "tasks", "cancelled", "INTEGER DEFAULT 0")
            # V0.12 : ordonnancement (deadline + durée estimée).
            _add_column_if_missing(self.conn, "tasks", "deadline", "TEXT DEFAULT ''")
            _add_column_if_missing(self.conn, "tasks", "estimated_minutes", "INTEGER DEFAULT 0")
            # V0.13 : rotation des agents (dernier échec → un autre reprend).
            _add_column_if_missing(self.conn, "tasks", "freedby", "TEXT DEFAULT ''")
            # V0.15 : taskflow — tasks = sujet (tag terminal), sub_tasks = relais.
            _add_column_if_missing(self.conn, "tasks", "tag", "TEXT DEFAULT ''")
            # V0.15 : description de la sub_task (consigne de l'étape, ex.
            # intels de l'exploration).
            _add_column_if_missing(self.conn, "sub_tasks", "description",
                                   "TEXT DEFAULT ''")
            # V0.17 : priorité de pioche par sub_task (le supervisor met à
            # jour ; le pick prend la plus haute pour l'agent).
            _add_column_if_missing(self.conn, "sub_tasks", "priority",
                                   "INTEGER DEFAULT 0")
            # V0.17 : chemin too_hard (doing → too_hard → bump/découpe).
            _add_column_if_missing(self.conn, "sub_tasks", "too_hard_count",
                                   "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "sub_tasks", "too_hard_reason",
                                   "TEXT DEFAULT ''")
            # V0.15 : dépendances qualifiées (étape + résultat attendus).
            _add_column_if_missing(self.conn, "task_dependencies", "required_tag",
                                   "TEXT DEFAULT ''")
            # V0.15 : dépendances entre sub_tasks (relais) — état + tag requis.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS sub_task_dependencies (
                    child_id       INTEGER NOT NULL,
                    parent_id      INTEGER NOT NULL,
                    required_state TEXT DEFAULT 'done',
                    required_tag   TEXT DEFAULT '',
                    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (child_id, parent_id),
                    FOREIGN KEY (child_id)  REFERENCES sub_tasks(sub_task_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES sub_tasks(sub_task_id) ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sub_task_deps_child "
                "ON sub_task_dependencies(child_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_sub_task_deps_parent "
                "ON sub_task_dependencies(parent_id)")
            # V0.10 : rôle requis → type de tâche (étape du pipeline). Une
            # tâche 'coder_senior' devient task_type 'coding' (le niveau de
            # l'agent borne la difficulté piochable). Clean des données (rien
            # de valeur à préserver en phase de dev).
            _migrate_role_required_to_task_type(self.conn)
            # Parenté des tâches (dépendance totale : tous les parents à l'état
            # requis pour débloquer l'enfant).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id        INTEGER NOT NULL,
                    parent_id      INTEGER NOT NULL,
                    required_state TEXT DEFAULT 'done',
                    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
                    PRIMARY KEY (task_id, parent_id),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                    FOREIGN KEY (parent_id) REFERENCES tasks(task_id) ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_deps_child "
                "ON task_dependencies(task_id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_deps_parent "
                "ON task_dependencies(parent_id)")
            # ── task_reports : rapports d'analyse attachés à une tâche ──
            # L'analyste (découpeur) produit des rapports d'analyse transmis à
            # la chaîne (coder/reviewer/merger) comme contexte. role = l'étape
            # qui a produit le rapport (analysis, testing, review…).
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS task_reports (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    task_id    INTEGER NOT NULL,
                    role       TEXT NOT NULL DEFAULT 'analysis',
                    content    TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (task_id) REFERENCES tasks(task_id)
                        ON DELETE CASCADE
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_task_reports_task "
                "ON task_reports(task_id)")
            _add_column_if_missing(self.conn, "issues", "team_id", "INTEGER DEFAULT -1")
            # V0.8.9 : lien issue → workspace d'analyse (le workspace où les
            # tasks de découpage vivent). Permet de marquer l'issue 'done'
            # quand toutes ses tasks sont terminées.
            _add_column_if_missing(self.conn, "issues", "analysis_workspace_id", "TEXT")
            # V0.8.9 : human_choice — choix humain requis (issue/task bloquée
            # en attente d'une réponse). L'agent signale via issue_block ; le
            # watcher débloque quand l'humain répond via l'API human_choice/*.
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS human_choice (
                    choice_id    TEXT PRIMARY KEY,
                    issue_id     INTEGER,
                    task_id      INTEGER,
                    question     TEXT NOT NULL,
                    options_json TEXT,
                    status       TEXT DEFAULT 'pending',
                    response     TEXT,
                    asked_at     INTEGER DEFAULT (strftime('%s','now')),
                    answered_at  INTEGER
                )
            """)
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_human_choice_status "
                "ON human_choice(status)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_human_choice_issue "
                "ON human_choice(issue_id)")
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass
        try:
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_role "
                "ON tasks(workspace_id, role_required, status, priority)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_tasks_team "
                "ON tasks(team_id, status, role_required)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_issues_team "
                "ON issues(team_id, status)")
        except Exception:
            pass

    def for_workspace(self, workspace_id: str) -> "WorkspaceScope":
        return WorkspaceScope(self.conn, workspace_id)

    def close(self):
        self.conn.close()


class WorkspaceScope:
    """Accès à toutes les données d'un workspace spécifique.

    Créé par WorkspaceDB.for_workspace().
    Les lectures sont paresseuses (SQLite lit page par page).
    """

    def __init__(self, conn: sqlite3.Connection, workspace_id: str):
        self.conn = conn
        self.wid = workspace_id
        self.tasks = TaskRepository(conn, workspace_id)
        self.issues = IssueRepository(conn, workspace_id)
        self.chat = ChatroomRepository(conn, workspace_id)
        self.usage = UsageFileRepository(conn, workspace_id)
        self.sub_tasks = SubTaskRepository(conn, workspace_id)
        self.ask = AskNewTaskRepository(conn, workspace_id)
        self.rules = SupervisorRulesRepository(conn, workspace_id)
        self.consensus = ConsensusRepository(conn, workspace_id)


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
                from modules.sql.agents_repo import AgentsDB
                db = AgentsDB()
                row = db.conn.execute(
                    "SELECT variables_json FROM agents WHERE agent_id = ?",
                    (int(str(agent_id).split("_")[-1]),)).fetchone()
                db.close()
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
