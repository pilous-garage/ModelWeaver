"""petri_runtime — miroir RUNTIME du pétri (observation PURE du FSM).

Sans toucher au FSM : le service à tick (10s) LIT les infos déjà générées
et reconstruit le MARQUAGE vivant du pétri :

  - agent_runtime.current_step + agents.state_json.current_step → où en est
    chaque agent dans son workflow (jeton d'activité) ;
  - sub_tasks (workspace.db) : status + assigned_to → les POTS EXTERNES
    (jetons data : unattributed/attributed/doing/done/supervised par type) ;
  - agent_runtime.thread_id → agent hydraté ou dead (déshydraté).

Sortie : un "snapshot de marquage" que la GUI / le diag peut afficher :
  {
    "agents": { "<agent>": {"step": "do_work", "state": "running|dead",
                            "flow": "main|pause|..." } },
    "data":   { "coding": {"doing": 2, "done": 3, "supervised": 1},
                "planning": {...} },
    "ts":     173...
  }

Le pétri STATIQUE (kind=petri) reste l'outil de vérification du workflow ;
ce module produit le marquage TEMPS RÉEL de l'exécution. Enregistré comme
service à tick (service_ticker), ~10s.

Usage:
    from services.petri_runtime import PetriRuntime
    pr = PetriRuntime()
    pr.tick()                    # une observation
    pr.snapshot()                # le marquage courant
    pr.start()                   # boucle 10s (thread daemon)
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sql.schema import mw_home

TICK_INTERVAL_S = 10.0

# états finaux (pot externes) — même nommage que le pétri statique.
ETATS = ("unattributed", "attributed", "doing", "done", "supervised",
         "cancelled", "waiting_dependencies")


def _agents_db() -> Any:
    from modules.sql.schema import _default_agents_db
    import sqlite3
    conn = sqlite3.connect(f"file:{_default_agents_db()}?mode=ro", uri=True,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def _workspace_db() -> Any:
    from modules.sql.workspace import _default_workspace_db
    import sqlite3
    conn = sqlite3.connect(f"file:{_default_workspace_db()}?mode=ro", uri=True,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


class PetriRuntime:
    """Observe les BDD et produit le marquage runtime du pétri."""

    def __init__(self, interval_s: float = TICK_INTERVAL_S):
        self.interval = interval_s
        self._stop = threading.Event()
        self._snapshot: Dict[str, Any] = {}
        self._thread: Optional[threading.Thread] = None

    # ── Observation (lecture pure, ne touche jamais au FSM) ──

    def observe_agents(self) -> Dict[str, Dict[str, Any]]:
        """agents hydratés + step courant + état (running/dead)."""
        out: Dict[str, Dict[str, Any]] = {}
        try:
            db = _agents_db()
            cur = db.execute("""
                SELECT a.agent_id, a.name, a.status, a.state_json,
                       r.current_step, r.thread_id, r.heartbeat_at
                FROM agents a
                LEFT JOIN agent_runtime r ON r.agent_id = a.agent_id
                WHERE a.status IN ('RUNNING','IDLE','INIT')
            """)
            for row in cur.fetchall():
                agent_id = row["agent_id"]
                st = row["status"]
                step = row["current_step"] or "idle"
                # state_json.current_step (persisté) en fallback
                if not step or step == "running":
                    try:
                        sj = json.loads(row["state_json"] or "{}")
                        step = sj.get("current_step", step)
                    except Exception:
                        pass
                state = "running" if (row["thread_id"] and st == "RUNNING") \
                    else "dead"
                out[row["name"] or f"agent:{agent_id}"] = {
                    "agent_id": agent_id, "step": step, "state": state,
                    "thread_id": row["thread_id"],
                    "heartbeat_at": row["heartbeat_at"],
                }
            db.close()
        except Exception:
            pass
        return out

    def observe_data(self) -> Dict[str, Dict[str, int]]:
        """Pots externes : comptage des sub_tasks par (type, état)."""
        out: Dict[str, Dict[str, int]] = {}
        try:
            db = _workspace_db()
            cur = db.execute(
                "SELECT sub_task_type, status, COUNT(*) AS n "
                "FROM sub_tasks GROUP BY sub_task_type, status")
            for row in cur.fetchall():
                t = row["sub_task_type"]
                out.setdefault(t, {})
                out[t][row["status"]] = row["n"]
            db.close()
        except Exception:
            pass
        return out

    def observe_assignments(self) -> List[Dict[str, Any]]:
        """sub_tasks en cours d'exécution (doing, assigned_to) — qui fait quoi."""
        out: List[Dict[str, Any]] = []
        try:
            db = _workspace_db()
            cur = db.execute(
                "SELECT sub_task_id, task_id, sub_task_type, status, "
                "assigned_to FROM sub_tasks WHERE status = 'doing' "
                "ORDER BY sub_task_id")
            for row in cur.fetchall():
                out.append({
                    "sub_task_id": row["sub_task_id"],
                    "task_id": row["task_id"],
                    "type": row["sub_task_type"],
                    "status": row["status"],
                    "assigned_to": row["assigned_to"],
                })
            db.close()
        except Exception:
            pass
        return out

    # ── Snapshot ────────────────────────────────────────────

    def snapshot(self) -> Dict[str, Any]:
        """Marquage runtime complet (agents + data + assignments)."""
        return {
            "agents": self.observe_agents(),
            "data": self.observe_data(),
            "assignments": self.observe_assignments(),
            "ts": time.time(),
        }

    def tick(self) -> Dict[str, Any]:
        """Une observation → met à jour le snapshot courant."""
        self._snapshot = self.snapshot()
        return self._snapshot

    def render(self) -> str:
        """Rendu texte lisible (diag console)."""
        snap = self._snapshot
        if not snap:
            snap = self.snapshot()
        lines = [f"== Pétri RUNTIME @ {snap.get('ts', 0):.0f} =="]
        for name, a in sorted((snap.get("agents") or {}).items()):
            lines.append(f"  agent {name:20s} {a['state']:8s} step={a['step']}")
        for t, counts in sorted((snap.get("data") or {}).items()):
            pieces = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            lines.append(f"  data {t:14s} {pieces}")
        for a in (snap.get("assignments") or [])[:10]:
            lines.append(f"  doing  #{a['sub_task_id']} {a['type']} "
                         f"→ {a['assigned_to']}")
        return "\n".join(lines)

    # ── Service à tick ──────────────────────────────────────

    def start(self) -> None:
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True,
                                        name="petri-runtime")
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()


def run_service() -> dict:
    """Point d'entrée service à tick (cmd services.petri_runtime:run_service)."""
    pr = PetriRuntime()
    return pr.tick()


__all__ = ["PetriRuntime", "run_service", "TICK_INTERVAL_S"]
