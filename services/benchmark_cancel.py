"""benchmark_cancel — PHASE CANCELLATION des tâches de benchmark.

Quand on annule/arrête un benchmark (ou un ensemble de tâches par branche de
développement), on ne supprime RIEN : on ANNULE proprement chaque tâche via
`workspace/cancel_task@v1` (le skill existant) qui :
  - envoie un signal KILL aux agents travaillant sur la tâche,
  - protège le travail sur une branche `canceled_<task_id>` du repo central,
  - reset le clone de l'agent au commit_start,
  - pose le flag `cancelled` (pas un statut) → les wakers/pickers l'ignorent.

Les tâches restent en base (archivées, réversibles). Le workspace est remis
dans un état propre pour le prochain benchmark.
"""

import os
from pathlib import Path


def _active_tasks_by_repo(workspace: str, repo_prefix: str = "") -> list:
    """Liste des tâches actives (todo/doing) d'un workspace, filtrées par
    préfixe de repo (ex. 'sessions/'). Retourne [{task_id, repo, assigned_to}]."""
    from modules.sql.workspace import WorkspaceDB
    wdb = WorkspaceDB()
    try:
        rows = wdb.conn.execute(
            "SELECT task_id, repo, assigned_to, status FROM tasks "
            "WHERE workspace_id = ? AND status IN ('todo','doing') "
            "AND COALESCE(cancelled, 0) = 0",
            (workspace,)).fetchall()
        out = []
        for r in rows:
            repo = r["repo"] or ""
            if repo_prefix and not repo.startswith(repo_prefix):
                continue
            out.append({"task_id": r["task_id"], "repo": repo,
                        "assigned_to": r["assigned_to"], "status": r["status"]})
        return out
    finally:
        wdb.close()


def cancel_session(workspace: str, session_repo: str,
                   home: str = "", reason: str = "") -> dict:
    """Annule TOUTES les tâches d'une session de benchmark.

    `session_repo` = le repo des tâches (ex. 'sessions/swarm-xxxx'). Chaque
    tâche active est annulée via cancel_task (signaux agents + branche
    canceled_<id> + flag cancelled). Retourne {ok, cancelled, count}.
    """
    from AgentsCatalogue.lib.workspacedb.task import cancel_task
    tasks = _active_tasks_by_repo(workspace, session_repo)
    if not tasks:
        return {"ok": True, "cancelled": [], "count": 0,
                "note": "aucune tâche active pour cette session"}
    cancelled = []
    errors = []
    for t in tasks:
        try:
            r = cancel_task({
                "workspace_id": workspace,
                "task_id": t["task_id"],
                "project_id": t["repo"] or "",
                "agent_id": t["assigned_to"] or "",
                "reason": reason or "annulation de session (benchmark arrêté)",
            }, home=home)
            if r.get("ok"):
                cancelled.extend(r.get("cancelled") or [t["task_id"]])
            else:
                errors.append({"task_id": t["task_id"], "error": r.get("error")})
        except Exception as e:  # noqa: BLE001
            errors.append({"task_id": t["task_id"], "error": str(e)[:200]})
    return {"ok": not errors, "cancelled": cancelled, "count": len(cancelled),
            "errors": errors}


def cancel_workspace(workspace: str, repo_prefix: str = "sessions/",
                     home: str = "", reason: str = "") -> dict:
    """Annule toutes les tâches de benchmark actives d'un workspace.

    `repo_prefix` limite l'annulation aux sessions de benchmark (ex.
    'sessions/'). Toutes les tâches actives d'une session sont annulées
    (signaux + branches protégées + flag cancelled), puis le workspace est
    laissé vide pour un prochain benchmark propre.
    """
    tasks = _active_tasks_by_repo(workspace, repo_prefix)
    if not tasks:
        return {"ok": True, "cancelled": [], "count": 0,
                "note": "aucune tâche de benchmark active"}
    # Grouper par session (repo) pour ne pas re-annuler deux fois un groupe.
    seen = set()
    total = []
    errors = []
    for t in tasks:
        key = t["repo"]
        if key in seen:
            continue
        seen.add(key)
        r = cancel_session(workspace, key, home=home, reason=reason)
        total.extend(r.get("cancelled") or [])
        errors.extend(r.get("errors") or [])
    # Nettoyage des repos de session (les .git des sessions annulées).
    cleanup = _cleanup_session_repos(workspace)
    return {"ok": not errors, "cancelled": total, "count": len(total),
            "errors": errors, "cleaned_repos": cleanup}


def _cleanup_session_repos(workspace: str) -> int:
    """Supprime les repos de session orphelins (sessions annulées, plus aucune
    tâche active). Retourne le nombre de repos nettoyés."""
    from modules.sql.workspace import WorkspaceDB
    import shutil
    wdb = WorkspaceDB()
    n = 0
    try:
        # Repos de session référencés par des tâches actives.
        active_repos = {r[0] for r in wdb.conn.execute(
            "SELECT DISTINCT repo FROM tasks WHERE workspace_id = ? "
            "AND status IN ('todo','doing') AND COALESCE(cancelled,0)=0 "
            "AND repo LIKE 'sessions/%'", (workspace,)).fetchall()}
        # Tous les repos de session existants.
        base = Path(os.environ.get("MODELWEAVER_HOME")
                    or Path.home() / ".modelweaver") / "repos" / "sessions"
        if base.is_dir():
            for d in base.iterdir():
                if not d.is_dir():
                    continue
                # Worktree `swarm-xxx` ou dépôt bare `swarm-xxx.git`.
                rel = f"sessions/{d.name}"
                if rel in active_repos:
                    continue
                is_worktree = (d / ".git").exists()
                is_bare = d.name.endswith(".git")
                if not (is_worktree or is_bare):
                    continue
                # Ne supprimer que si AUCUNE tâche active ne référence la
                # session (peu importe worktree ou bare).
                shutil.rmtree(d, ignore_errors=True)
                n += 1
    finally:
        wdb.close()
    return n
