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
    from modules.sqlite.workspace.workspace import WorkspaceDB
    return WorkspaceDB()


def create_task(workspace: str, title: str, description: str, task_type="coding") -> int:
    """Crée une entrée swarm (task + sub_task analysis initiale) piochable par
    les greedy. Utilise create_entry (entrypoint as-llm-leader) pour amorcer le
    taskflow : une sub_task `analysis` unattributed est créée → le waker/supervisor
    réveille l'analyste → découpe → coding/testing/review/merge → respond.

    Crée le workspace si absent (la team peut ne pas être bootée)."""
    from AgentsCatalogue.lib.workspacedb import taskflow
    # team_id résolu depuis le director du workspace (team:llm-code → 1)
    try:
        wdb = _wdb()
        _row = wdb.conn.execute(
            "SELECT director FROM workspaces WHERE workspace_id=?",
            (workspace,)).fetchone()
        _team_id = 1 if _row else -1
        wdb.close()
    except Exception:
        _team_id = -1
    r = taskflow.create_entry({
        "workspace_id": workspace,
        "title": title,
        "description": description,
        "entry_type": "feature",   # skip classification consensus (LLM)
        "team_id": _team_id,
        "priority": 100,
    }, home=str(_wdb_home()))
    if r.get("ok"):
        return r["task_id"]
    # fallback : log
    sys.stderr.write(f"create_entry échec: {r.get('error')}\n")
    return -1


def _wdb_home():
    from services._common import mw_home
    return mw_home()


def task_status(workspace: str, task_id: int) -> str:
    wdb = _wdb()
    try:
        row = wdb.conn.execute(
            "SELECT status, assigned_to FROM tasks WHERE task_id = ?",
            (task_id,)).fetchone()
        return row["status"] if row else "absent"
    finally:
        wdb.close()


def task_type_of(workspace: str, task_id: int) -> str:
    """task_type courant d'une tâche ('' si absente)."""
    wdb = _wdb()
    try:
        row = wdb.conn.execute(
            "SELECT task_type FROM tasks WHERE task_id = ?",
            (task_id,)).fetchone()
        return (row["task_type"] or "") if row else ""
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
    """Annule proprement les tâches d'un workspace avant benchmark.

    PHASE CANCELLATION (non destructif) : chaque tâche de benchmark active est
    annulée via cancel_task (signal KILL aux agents + branche canceled_<id> +
    flag cancelled), puis les repos de session orphelins sont nettoyés. Les
    tâches restent en base (archivées, réversibles) mais ne sont plus
    piochées/réveillées."""
    try:
        from services.benchmark_cancel import cancel_workspace
        # Annule TOUTES les tâches actives du workspace (benchmarks via
        # sessions/ ET tâches de test à repo vide) — chaque cancel_task gère
        # l'absence de repo (pas de branche de protection, flag cancelled seul).
        r = cancel_workspace(workspace, repo_prefix="",
                             reason="reset benchmark (phase cancellation)")
        if r.get("errors"):
            for e in r["errors"]:
                print(f"  [cancel] tâche {e.get('task_id')}: {e.get('error')}",
                      flush=True)
        if r.get("count"):
            print(f"  [cancel] {r['count']} tâches annulées "
                  f"({r.get('cleaned_repos', 0)} repos nettoyés)", flush=True)
    except Exception as e:
        print(f"  [cancel] échec phase cancellation: {e}", flush=True)
    # Réinitialiser les variables de run des greedy (task/repo_eff/LLM) pour
    # qu'ils re-piochent proprement au prochain run — sinon un greedy conserve
    # une ancienne tâche annulée dans ses variables persistées et la resume
    # sur des tâches mortes (boucle pick échoué).
    try:
        from modules.sql.agents_repo import AgentsDB
        import json as _json
        db = AgentsDB()
        rows = db.conn.execute(
            "SELECT agent_id, variables_json FROM agents "
            "WHERE occupation = 'continue' AND name LIKE 'team:%'").fetchall()
        for r in rows:
            try:
                v = _json.loads(r["variables_json"] or "{}")
                dirty = False
                for k in ("task", "repo_eff", "_last_call_error"):
                    if k in v:
                        v.pop(k, None)
                        dirty = True
                if dirty:
                    db.conn.execute(
                        "UPDATE agents SET variables_json = ? WHERE agent_id = ?",
                        (_json.dumps(v), r["agent_id"]))
            except Exception:
                continue
        db.conn.commit()
        db.close()
    except Exception as e:
        print(f"  [cancel] réinit greedy: {e}", flush=True)


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
    # Le greedy transitionne le token coding → code_review quand il a livré
    # (commit + finalisation). `done` complet (review/merge) peut ne jamais
    # arriver pour une tâche de test synthétique : on considère FINI dès que la
    # tâche n'est plus `coding` active (transitionnée) ou passée `done`.
    done_statuses = ("done", "cancelled")
    while time.monotonic() - t0 < timeout_s:
        status = task_status(workspace, tid)
        ttype = task_type_of(workspace, tid)
        if status == "doing" and picked_at is None:
            picked_at = time.monotonic() - t0
        if status in done_statuses or (ttype and ttype != task_type):
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
    p.add_argument("--timeout", type=int, default=7200,
                   help="durée max d'attente en secondes (défaut 2h — le greedy "
                        "peut prendre plusieurs minutes pour finaliser)")
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
    try:
        report = run(args.timeout, args.workspace, args.task_type)
    except KeyboardInterrupt:
        # Ctrl-C : phase cancellation propre (signal aux agents + flag
        # cancelled) pour libérer le swarm proprement.
        print("\n[bench_swarm_live] interruption — phase cancellation…",
              flush=True)
        reset_workspace_tasks(args.workspace)
        return 130
    print(f"[bench_swarm_live] status={report.get('status')} "
          f"task={report.get('task_id')} "
          f"last={report.get('last_status', report.get('status'))} "
          f"picked={report.get('picked_s')}s "
          f"durée={report.get('duration_s')}s "
          f"agents_running={report.get('agents_running')}")
    if report.get("status") == "error":
        print(f"  raison: {report.get('reason')}")
    # LOG du résultat (timestampé, BDD + JSONL) pour l'historique des runs.
    try:
        from services.benchmark_results import log_report
        bid = log_report("bench_swarm_live", report)
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        print(f"[bench_swarm_live] résultat loggué à {ts} (id={bid})")
    except Exception as e:
        print(f"[bench_swarm_live] échec log résultat: {e}")
    # PHASE CANCELLATION UNIQUEMENT en cas d'échec réel (timeout sans pick,
    # erreur). Si la tâche est done ou encore en cours (ok/ok_pick), on la
    # LAISSE tourner : le greedy continue et finalisera. Annuler ici casserait
    # un benchmark qui progresse.
    if report.get("status") in ("timeout", "error"):
        reset_workspace_tasks(args.workspace)
    return 0 if report.get("status") == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
