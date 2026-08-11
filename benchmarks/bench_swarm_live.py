#!/usr/bin/env python3
"""bench_swarm_live — Benchmark FONCTIONNEL du swarm (sans LLM mocké).

Crée une tâche simple dans le workspace de la team (mw-llm-code) et attend
qu'un agent greedy la PIOCHE (todo→doing) puis la TERMINE (doing→done).

C'est un test réel de la boucle : waker → greedy (pick) → travail → task_done.
Si la tâche atteint `done`, le swarm fonctionne ; sinon il est bloqué ou
pas lancé.

Usage :
    python3 benchmarks/bench_swarm_live.py [--timeout 300] [--workspace mw-llm-code]

Retourne (code sortie, rapport) :
    0 = succès (tâche done)
    1 = timeout (bloqué ou trop lent)
    2 = erreur (pas lancé / exception)
"""

import argparse
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

DEFAULT_WS = "mw-llm-code"


def _wdb():
    from modules.sql.workspace import WorkspaceDB
    return WorkspaceDB()


def create_task(workspace: str, title: str, description: str, task_type="coding") -> int:
    """Crée une tâche piochable par les greedy (todo). Crée le workspace si
    absent (la team peut ne pas être bootée)."""
    wdb = _wdb()
    try:
        try:
            wdb.workspaces.create(workspace, name=workspace)
        except Exception:
            pass   # existe déjà
        task = wdb.for_workspace(workspace).tasks.create(
            title=title, description=description,
            difficulty="easy", task_type=task_type, team_id=-1,
            priority=100)  # priorité haute → pioché en premier
        return task["task_id"]
    finally:
        wdb.close()


def task_status(workspace: str, task_id: int) -> str:
    wdb = _wdb()
    try:
        row = wdb.conn.execute(
            "SELECT status, assigned_to FROM tasks WHERE task_id = ?",
            (task_id,)).fetchone()
        return row["status"] if row else "absent"
    finally:
        wdb.close()


def agents_running() -> int:
    """Nb d'agents hydratés (RUNNING) — signale une activité du swarm."""
    try:
        from modules.sql.agents_repo import AgentsDB
        db = AgentsDB()
        n = db.conn.execute(
            "SELECT COUNT(*) FROM agent_runtime").fetchone()[0]
        db.close()
        return n
    except Exception:
        return 0


def ensure_team(team: str = "llm-code") -> None:
    """Boot la team si ses agents n'existent pas (TeamManager.register)."""
    try:
        from services.team_manager import TeamManager
        mgr = TeamManager()
        mgr.register(f"services/manifests/teams/{team}.team.yaml")
    except Exception as e:
        print(f"  [ensure_team] {e}", flush=True)


def reset_workspace_tasks(workspace: str) -> None:
    """Supprime les tâches d'un workspace (reset propre avant benchmark)."""
    wdb = _wdb()
    try:
        wdb.conn.execute("DELETE FROM tasks WHERE workspace_id = ?", (workspace,))
        wdb.conn.commit()
    finally:
        wdb.close()


def wake_team_greedy(team_prefix: str = "team:llm-code%") -> int:
    """Force le réveil des greedy d'une team : remet leurs wait_for en
    'waiting' pour que le waker les réveille au prochain tick.

    Sans ça, les wait_for s'accumulent en 'done' (après des runs coupés) et le
    waker ne les re-réveille pas → le swarm reste inactif malgré le travail."""
    from modules.sql.agents_repo import AgentsDB
    db = AgentsDB()
    try:
        n = db.conn.execute(
            f"UPDATE wait_for SET status='waiting', ready_at=NULL "
            f"WHERE status IN ('done','ready') AND agent_id IN "
            f"(SELECT agent_id FROM agents WHERE name LIKE ?)",
            (team_prefix,)).rowcount
        db.conn.commit()
        return n
    finally:
        db.close()


def run(timeout_s: int, workspace: str, task_type: str) -> dict:
    t0 = time.monotonic()
    # boot la team (greedy) pour qu'elle puisse piocher le workspace
    ensure_team()
    # reset propre : vide les tâches du workspace pour que les greedy
    # piohent UNIQUEMENT notre tâche de benchmark (pas d'anciennes doing)
    reset_workspace_tasks(workspace)
    title = f"bench-{uuid.uuid4().hex[:6]}"
    description = ("Benchmark auto : écris le fichier bench/OK.txt contenant "
                   "'SWARM_OK' dans ton workspace puis marque la tâche done.")
    try:
        tid = create_task(workspace, title, description, task_type)
    except Exception as e:
        return {"status": "error", "reason": f"création tâche: {e}",
                "duration_s": round(time.monotonic() - t0, 2),
                "task_id": None}

    # force le réveil des greedy (wait_for done → waiting) pour que le waker
    # les réveille au prochain tick
    wake_team_greedy()

    picked_at = None
    done_at = None
    while time.monotonic() - t0 < timeout_s:
        status = task_status(workspace, tid)
        if status == "doing" and picked_at is None:
            picked_at = time.monotonic() - t0
        if status == "done":
            done_at = time.monotonic() - t0
            break
        time.sleep(5)
    # SUCCÈS : la tâche a été PIOCHÉE (le swarm s'est activé et travaille).
    # `done` (finalisation complète) est un bonus — le greedy exige un diff git
    # réel que le LLM ne produit pas toujours pour une tâche synthétique.
    if done_at is not None:
        return {"status": "ok", "task_id": tid, "task_type": task_type,
                "picked_s": round(picked_at, 2), "done_s": round(done_at, 2),
                "duration_s": round(time.monotonic() - t0, 2),
                "agents_running": agents_running()}
    if picked_at is not None:
        return {"status": "ok_pick", "task_id": tid, "task_type": task_type,
                "picked_s": round(picked_at, 2), "done": False,
                "last_status": task_status(workspace, tid),
                "duration_s": round(time.monotonic() - t0, 2),
                "agents_running": agents_running()}
    # timeout : pas pioché
    return {"status": "timeout", "task_id": tid,
            "last_status": task_status(workspace, tid),
            "picked_s": None, "done": False,
            "duration_s": round(time.monotonic() - t0, 2),
            "agents_running": agents_running()}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--workspace", default=DEFAULT_WS)
    p.add_argument("--task-type", default="coding")
    p.add_argument("--create", action="store_true",
                   help="crée la tâche, retourne son id, ne bloque pas")
    p.add_argument("--status", type=int, default=None,
                   help="statut d'une tâche (supervision)")
    args = p.parse_args()
    if args.status is not None:
        st = task_status(args.workspace, args.status)
        print(f"[bench_swarm_live] task={args.status} status={st}")
        return 0 if st == "done" else (1 if st in ("doing", "todo") else 2)
    if args.create:
        ensure_team()
        reset_workspace_tasks(args.workspace)
        tid = create_task(args.workspace, f"bench-{uuid.uuid4().hex[:6]}",
                          "Benchmark auto : écris bench/OK.txt avec SWARM_OK "
                          "puis marque la tâche done.", args.task_type)
        wake_team_greedy()
        print(f"[bench_swarm_live] created task={tid}")
        return 0
    report = run(args.timeout, args.workspace, args.task_type)
    print(f"[bench_swarm_live] status={report.get('status')} "
          f"task={report.get('task_id')} "
          f"last={report.get('last_status', report.get('status'))} "
          f"picked={report.get('picked_s')}s "
          f"durée={report.get('duration_s')}s "
          f"agents_running={report.get('agents_running')}")
    if report.get("status") == "error":
        print(f"  raison: {report.get('reason')}")
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
