"""ProjectSupervisor — orchestration de projets multi-agents.

Le supervisor est lui-même un agent (role_type='supervisor') enregistré
auprès de l'AgentManager via `spawn()`. Il profite ainsi du même cycle
de vie : heartbeat, preemption, kill, FSM steps.

Usage:
    manager = AgentManager()
    sup = ProjectSupervisor(project_name="my-project")
    sup.spawn_as_agent(manager, provider_ref="openrouter")
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING


@dataclass
class Task:
    task_id: str
    description: str
    assigned_agent: Optional[str] = None
    status: str = "pending"  # pending | in_progress | done | failed | blocked
    depends_on: List[str] = field(default_factory=list)
    result: Optional[str] = None
    created_at: float = 0.0
    started_at: Optional[float] = None
    completed_at: Optional[float] = None


@dataclass
class ProjectBudget:
    total_tokens: int = 0
    used_tokens: int = 0
    total_cost_usd: float = 0.0
    used_cost_usd: float = 0.0
    max_tokens: int = 0
    max_cost_usd: float = 0.0

    @property
    def tokens_remaining(self) -> int:
        return self.max_tokens - self.used_tokens

    @property
    def cost_remaining(self) -> float:
        return self.max_cost_usd - self.used_cost_usd

    @property
    def over_budget(self) -> bool:
        return (self.max_tokens > 0 and self.used_tokens >= self.max_tokens) or (
            self.max_cost_usd > 0 and self.used_cost_usd >= self.max_cost_usd
        )


class ProjectSupervisor:
    """Superviseur de projet : orchestre une équipe d'agents.

    Instancié par projet (project_name fixe au constructeur).
    L'enregistrement en tant qu'agent se fait via spawn_as_agent()
    auprès de l'AgentManager. Le supervisor devient alors un
    agent FSM à part entière (heartbeat, preemption, kill).
    """

    def __init__(self, project_name: str,
                 db: Optional[sqlite3.Connection] = None):
        self.project_name = project_name
        self.db = db or self._open_project_db()
        self._ensure_project_schema()

    # ── Agent spawn ──────────────────────────────────

    def spawn_as_agent(self, manager: "AgentManager",
                       provider_ref: str = "",
                       model_ref: str = "",
                       resources: Optional[Dict[str, Any]] = None,
                       occupation: str = "continue") -> Dict[str, Any]:
        """Enregistre ce supervisor comme agent dans AgentManager.

        Le supervisor apparaît dans list_active() et bénéficie du
        ciclo de vie standard (heartbeat, preemption, kill).
        L'orchestration loop est déclenchée par les FSM steps.
        """
        config = {
            "role": "supervisor",
            "project_name": self.project_name,
            "step": "idle",
        }
        if resources is None:
            resources = {"llm": True, "priority": 1, "preemptible": False}
        return manager.spawn_agent(
            name=f"supervisor-{self.project_name}",
            role="supervisor",
            occupation=occupation,
            provider_ref=provider_ref,
            model_ref=model_ref,
            resources=resources,
            config=config,
            keep_sleeping=True,
        )

    def get_agent_name(self) -> str:
        return f"supervisor-{self.project_name}"

    # ── DB helpers ─────────────────────────────────────────

    def _open_project_db(self) -> sqlite3.Connection:
        from services._common import mw_home
        path = mw_home() / "projects.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(str(path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _ensure_project_schema(self):
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS project_tasks (
                task_id TEXT PRIMARY KEY,
                project_name TEXT NOT NULL,
                description TEXT NOT NULL,
                assigned_agent TEXT,
                status TEXT DEFAULT 'pending',
                depends_on TEXT DEFAULT '[]',
                result TEXT,
                created_at REAL,
                started_at REAL,
                completed_at REAL
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS project_budget (
                project_name TEXT PRIMARY KEY,
                total_tokens INTEGER DEFAULT 0,
                used_tokens INTEGER DEFAULT 0,
                total_cost_usd REAL DEFAULT 0.0,
                used_cost_usd REAL DEFAULT 0.0,
                max_tokens INTEGER DEFAULT 0,
                max_cost_usd REAL DEFAULT 0.0
            )
        """)
        self.db.execute("""
            CREATE TABLE IF NOT EXISTS project_state (
                project_name TEXT PRIMARY KEY,
                status TEXT DEFAULT 'setup',
                workflow JSON DEFAULT '[]',
                current_step INTEGER DEFAULT 0,
                updated_at REAL
            )
        """)
        self.db.commit()

    # ── Task queue ──────────────────────────────────────────

    def add_task(self, project_name: str, task_id: str,
                 description: str, depends_on: Optional[List[str]] = None,
                 assigned_agent: Optional[str] = None) -> Task:
        task = Task(
            task_id=task_id,
            description=description,
            assigned_agent=assigned_agent,
            depends_on=depends_on or [],
            created_at=time.time(),
        )
        self.db.execute(
            "INSERT OR REPLACE INTO project_tasks "
            "(task_id, project_name, description, assigned_agent, status, "
            "depends_on, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (task_id, project_name, description, assigned_agent,
             task.status, json.dumps(task.depends_on), task.created_at),
        )
        self.db.commit()
        return task

    def get_ready_tasks(self, project_name: str) -> List[Task]:
        """Retourne les tâches 'pending' dont toutes les dépendances sont 'done'."""
        rows = self.db.execute(
            "SELECT * FROM project_tasks WHERE project_name = ? AND status = 'pending'",
            (project_name,),
        ).fetchall()
        ready = []
        for r in rows:
            deps = json.loads(r["depends_on"] or "[]")
            if not deps:
                ready.append(self._row_to_task(r))
                continue
            all_done = all(
                self._task_status(project_name, d) == "done" for d in deps
            )
            if all_done:
                ready.append(self._row_to_task(r))
        return ready

    def _task_status(self, project_name: str, task_id: str) -> str:
        row = self.db.execute(
            "SELECT status FROM project_tasks WHERE task_id = ? AND project_name = ?",
            (task_id, project_name),
        ).fetchone()
        return row["status"] if row else "missing"

    def assign_task(self, project_name: str, task_id: str, agent_name: str) -> bool:
        row = self.db.execute(
            "UPDATE project_tasks SET assigned_agent = ?, status = 'in_progress', "
            "started_at = ? WHERE task_id = ? AND project_name = ? AND status = 'pending'",
            (agent_name, time.time(), task_id, project_name),
        )
        self.db.commit()
        return row.rowcount > 0

    def complete_task(self, project_name: str, task_id: str,
                      result: str = "", success: bool = True):
        status = "done" if success else "failed"
        self.db.execute(
            "UPDATE project_tasks SET status = ?, result = ?, "
            "completed_at = ? WHERE task_id = ? AND project_name = ?",
            (status, result, time.time(), task_id, project_name),
        )
        self.db.commit()

    # ── Budget ──────────────────────────────────────────────

    def set_budget(self, project_name: str, max_tokens: int = 0,
                   max_cost_usd: float = 0.0):
        self.db.execute(
            "INSERT OR REPLACE INTO project_budget "
            "(project_name, max_tokens, max_cost_usd) VALUES (?, ?, ?)",
            (project_name, max_tokens, max_cost_usd),
        )
        self.db.commit()

    def record_usage(self, project_name: str, tokens: int, cost_usd: float) -> ProjectBudget:
        self.db.execute(
            "UPDATE project_budget SET used_tokens = used_tokens + ?, "
            "used_cost_usd = used_cost_usd + ? WHERE project_name = ?",
            (tokens, cost_usd, project_name),
        )
        self.db.commit()
        return self.get_budget(project_name)

    def get_budget(self, project_name: str) -> ProjectBudget:
        row = self.db.execute(
            "SELECT * FROM project_budget WHERE project_name = ?",
            (project_name,),
        ).fetchone()
        if not row:
            return ProjectBudget()
        return ProjectBudget(
            total_tokens=row["total_tokens"],
            used_tokens=row["used_tokens"],
            total_cost_usd=row["total_cost_usd"],
            used_cost_usd=row["used_cost_usd"],
            max_tokens=row["max_tokens"],
            max_cost_usd=row["max_cost_usd"],
        )

    # ── Workflow orchestration ──────────────────────────────

    def set_workflow(self, project_name: str, steps: List[Dict[str, Any]]):
        """Définir le workflow d'étapes ordonnées du projet.

        Chaque step: {\"name\": str, \"tasks\": [str], \"depends_on\": [str]}
        """
        self.db.execute(
            "INSERT OR REPLACE INTO project_state "
            "(project_name, status, workflow, current_step, updated_at) "
            "VALUES (?, 'running', ?, 0, ?)",
            (project_name, json.dumps(steps), time.time()),
        )
        self.db.commit()

    def advance_step(self, project_name: str) -> Optional[int]:
        """Avance à l'étape suivante si toutes ses tâches sont done."""
        row = self.db.execute(
            "SELECT workflow, current_step FROM project_state WHERE project_name = ?",
            (project_name,),
        ).fetchone()
        if not row:
            return None
        steps = json.loads(row["workflow"] or "[]")
        current = row["current_step"]
        if current >= len(steps):
            return None
        step = steps[current]
        step_tasks = step.get("tasks", [])
        all_done = all(
            self._task_status(project_name, t) == "done" for t in step_tasks
        )
        if all_done:
            next_step = current + 1
            self.db.execute(
                "UPDATE project_state SET current_step = ?, "
                "status = ? WHERE project_name = ?",
                (next_step, "step_done" if next_step >= len(steps) else "running",
                 project_name),
            )
            self.db.commit()
        return current

    # ── Project state ───────────────────────────────────────

    def create_project(self, project_name: str, description: str = "") -> Dict[str, Any]:
        self.db.execute(
            "INSERT OR REPLACE INTO project_state "
            "(project_name, status, workflow, current_step, updated_at) "
            "VALUES (?, 'created', '[]', 0, ?)",
            (project_name, time.time()),
        )
        self.db.commit()
        return {"project": project_name, "status": "created", "description": description}

    def get_project_status(self, project_name: str) -> Dict[str, Any]:
        row = self.db.execute(
            "SELECT * FROM project_state WHERE project_name = ?",
            (project_name,),
        ).fetchone()
        if not row:
            return {"project": project_name, "status": "not_found"}
        tasks = self.db.execute(
            "SELECT status, assigned_agent FROM project_tasks WHERE project_name = ?",
            (project_name,),
        ).fetchall()
        by_status = defaultdict(int)
        for t in tasks:
            by_status[t["status"]] += 1
        budget = self.get_budget(project_name)
        return {
            "project": project_name,
            "status": row["status"],
            "current_step": row["current_step"],
            "tasks": {
                "total": len(tasks),
                "pending": by_status.get("pending", 0),
                "in_progress": by_status.get("in_progress", 0),
                "done": by_status.get("done", 0),
                "failed": by_status.get("failed", 0),
            },
            "budget": {
                "max_tokens": budget.max_tokens,
                "used_tokens": budget.used_tokens,
                "max_cost_usd": budget.max_cost_usd,
                "used_cost_usd": budget.used_cost_usd,
                "over_budget": budget.over_budget,
            },
        }

    def stop_project(self, project_name: str):
        self.db.execute(
            "UPDATE project_state SET status = 'stopped', updated_at = ? "
            "WHERE project_name = ?",
            (time.time(), project_name),
        )
        self.db.commit()

    # ── Supervisor loop ──────────────────────────────────────

    def tick(self) -> Dict[str, Any]:
        """Cycle de supervision : heartbeat + distribue tâches prêtes."""
        agent_result = self.manager.tick()
        distributed = 0

        projects = self.db.execute(
            "SELECT project_name FROM project_state WHERE status = 'running'"
        ).fetchall()
        for proj in projects:
            name = proj["project_name"]
            ready = self.get_ready_tasks(name)
            for task in ready:
                agent = self._pick_agent_for_task(name, task)
                if agent:
                    self.assign_task(name, task.task_id, agent)
                    distributed += 1

        return {
            "agent_ticker": agent_result,
            "tasks_distributed": distributed,
        }

    def _pick_agent_for_task(self, project_name: str, task: Task) -> Optional[str]:
        """Choisit l'agent disponible pour une tâche."""
        if task.assigned_agent:
            agent = self.manager.get_by_name(task.assigned_agent)
            if agent and agent["status"] in ("IDLE", "RUNNING"):
                return task.assigned_agent

        active = self.manager.list_active()
        if not active:
            return None
        active_sorted = sorted(active, key=lambda a: a.get("last_active_at", ""))
        return active_sorted[0].get("name")

    def _row_to_task(self, row: sqlite3.Row) -> Task:
        return Task(
            task_id=row["task_id"],
            description=row["description"],
            assigned_agent=row["assigned_agent"],
            status=row["status"],
            depends_on=json.loads(row["depends_on"] or "[]"),
            result=row["result"],
            created_at=row["created_at"] or 0.0,
            started_at=row["started_at"],
            completed_at=row["completed_at"],
        )