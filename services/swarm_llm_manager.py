#!/usr/bin/env python3
"""swarm_llm_manager — Orchestration complète d'un /v1/chat/completions.

Le manager SIMULE un vrai LLM face à un humain : il reçoit la prompt telle
quelle (et les éventuels fichiers/tool_calls JSON), crée un dépôt local
(« git-bazzar ») avec une branche vierge, y dépose la prompt, délègue le
découpage à l'ANALYSTE, laisse les greedy (codeur/reviewer/tester/merger)
travailler par rôle, puis récupère les fichiers produits et renvoie
**fichiers + résumé** au format OpenAI.

Flux :
  1. create_session(prompt, files) → repo local + branche vierge + prompt.txt
  2. ask_analyst(session)          → l'analyste découpe en tâches par rôle
  3. supervise(session)            → attend que les tâches soient done
  4. collect(session)              → lit les fichiers créés + résumé
  5. reply(session)                → format OpenAI {choices, content, files}
"""

from __future__ import annotations

import json
import os
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
MW_HOME = Path(os.environ.get("MODELWEAVER_HOME") or Path.home() / ".modelweaver")

# Le dépôt central (repo de base pour les greedy qui ont besoin d'un clone).
CENTRAL_REPO = MW_HOME / "repos" / "mw-swarm.git"
# Les dépôts de session (git-bazzar) : un par requête benchmark.
SESSION_REPOS = MW_HOME / "repos" / "sessions"

ANALYST_AGENT = "team:llm-code/analyst"
WORKSPACE = "mw-llm-code"
TEAM_ID = 519


# ── helpers git ──────────────────────────────────────────────────────────

def _git(repo: Path, *args: str) -> str:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=30)
    return r.stdout.strip()


def _git_ok(repo: Path, *args: str) -> bool:
    r = subprocess.run(["git", "-C", str(repo), *args],
                       capture_output=True, text=True, timeout=30)
    return r.returncode == 0


# ── session (repo git-bazzar par benchmark) ──────────────────────────────

def create_session(prompt: str, files: Optional[Dict[str, str]] = None,
                   session_id: str = "") -> Dict[str, Any]:
    """Crée un dépôt local vierge + une branche, y dépose prompt + fichiers.

    Retourne {session_id, repo, branch, workspace_id}."""
    session_id = session_id or f"swarm-{uuid.uuid4().hex[:10]}"
    repo = SESSION_REPOS / session_id
    SESSION_REPOS.mkdir(parents=True, exist_ok=True)
    if repo.exists():
        import shutil
        shutil.rmtree(repo, ignore_errors=True)
    repo.mkdir(parents=True)
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "swarm@modelweaver.local")
    _git(repo, "config", "user.name", "swarm")
    # branche vierge de travail
    branch = f"autocode/{session_id}"
    _git(repo, "checkout", "-b", branch)

    # dépose la prompt originelle
    (repo / "PROMPT.md").write_text(prompt, encoding="utf-8")
    # dépose les éventuels fichiers fournis (tool_calls / JSON)
    for name, content in (files or {}).items():
        f = repo / name
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(str(content), encoding="utf-8")

    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "seed: prompt + fichiers initiaux")

    return {"session_id": session_id, "repo": str(repo), "branch": branch,
            "workspace_id": WORKSPACE}


# ── analyste (découpage) ────────────────────────────────────────────────

ANALYST_PROMPT = (
    "On m'a envoyé ce prompt. Analyse son genre, sa complexité, s'il faut "
    "créer un ou des fichiers, puis découpe-le en sous-tâches pour que mes "
    "agents de code puissent travailler dessus.\n\n"
    "PROMPT REÇU :\n{prompt}\n\n"
    "Le projet est dans ton workspace (clone via git_clone project_id={repo}). "
    "Crée les tâches workspace (task_create) avec les rôles appropriés "
    "(coding pour le code, testing pour les tests, etc.). Chaque tâche doit "
    "être indépendante et concrète. Réponds avec le plan et la liste des "
    "tâches créées."
)


def ask_analyst(session: Dict[str, Any]) -> Dict[str, Any]:
    """Envoie la prompt d'analyse à l'analyste (via dev-chat / agent_call).

    L'analyste découpe en tâches workspace par rôle. Retourne {ok, ...}."""
    prompt = (session.get("_prompt") or "")
    repo = session.get("repo", "")
    # project_id RELATIF (sessions/<id>) pour git_clone : le chemin absolu
    # cassait la résolution _central_repo (mw_home()/repos//abs/path/…).
    session_id = session.get("session_id", "")
    if session_id:
        repo_rel = f"sessions/{session_id}"
    else:
        repo_rel = repo
    message = ANALYST_PROMPT.format(prompt=prompt, repo=repo_rel)
    try:
        from services.api.handlers.dev_chat import op_dev_chat_send
        res = op_dev_chat_send({
            "message": message,
            "mode": "build",
            "session": f"analyst-{session['session_id']}",
            "workspace_id": WORKSPACE,
        })
        return {"ok": res.get("status") in ("ok", "success"),
                "result": res}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": str(e)}


# ── supervision ──────────────────────────────────────────────────────────

def pending_tasks_count(workspace: str = WORKSPACE) -> int:
    """Tâches non terminées (todo/doing) dans le workspace."""
    from modules.sql.workspace import WorkspaceDB
    wdb = WorkspaceDB()
    try:
        n = wdb.conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE workspace_id=? "
            "AND status IN ('todo','doing')", (workspace,)).fetchone()[0]
        return n
    finally:
        wdb.close()


def supervise(session: Dict[str, Any], timeout_s: int = 600,
              poll_s: int = 15) -> Dict[str, Any]:
    """Attend que toutes les tâches du workspace soient done (ou timeout)."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout_s:
        n = pending_tasks_count()
        if n == 0:
            return {"ok": True, "elapsed_s": round(time.monotonic() - t0, 1)}
        time.sleep(poll_s)
    return {"ok": False, "error": f"timeout ({timeout_s}s), {n} tâches restantes",
            "elapsed_s": round(time.monotonic() - t0, 1)}


# ── collecte + réponse ───────────────────────────────────────────────────

def collect(session: Dict[str, Any]) -> Dict[str, Any]:
    """Lit les fichiers créés dans la branche (hors PROMPT.md) + le résumé."""
    repo = Path(session["repo"])
    files = {}
    for f in sorted(repo.rglob("*")):
        if f.is_file() and f.name != "PROMPT.md" and ".git" not in f.parts:
            try:
                files[str(f.relative_to(repo))] = f.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                pass
    # le résumé = le contenu de RESUME.md si créé, sinon la prompt seed
    summary = files.pop("RESUME.md", None) or session.get("_prompt", "")
    return {"files": files, "summary": summary}


def reply(session: Dict[str, Any], collect_res: Dict[str, Any]) -> Dict[str, Any]:
    """Construit la réponse au FORMAT LLM (OpenAI chat.completion).

    `content` = le texte que le LLM renverrait à l'humain : résumé + fichiers
    produits (le benchmark le lit comme la réponse d'un modèle). C'est un
    vrai LLM qu'on simule, pas une API custom."""
    files = collect_res.get("files", {})
    summary = collect_res.get("summary", "")
    # Construit le texte assistant (comme un LLM qui répond à l'humain)
    parts = [f"### Résumé\n{summary}"] if summary else []
    for name, content in files.items():
        parts.append(f"### {name}\n```\n{content}\n```")
    content = "\n\n".join(parts) if parts else "(aucun fichier produit)"
    return {
        "ok": True,
        "object": "chat.completion",
        "model": "mw-swarm",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0},
        "files": files, "summary": summary,
        "swarm": {"session": session["session_id"],
                  "branch": session.get("branch", "")},
    }


# ── orchestration complète ───────────────────────────────────────────────

def run_completion(prompt: str, files: Optional[Dict[str, str]] = None,
                   timeout_s: int = 600) -> Dict[str, Any]:
    """Exécute le flux complet d'un /v1/chat/completions — TASKFLOW V0.15.

    Retourne une réponse OpenAI {choices, files, summary} ou une erreur.

    Nouveau flux (sans LLM côté entrée) :
      1. as_llm_leader → create_entry : task `chat_entry` + sub_task analysis.
      2. Le taskflow tourne automatiquement (amorce → greedy : analyste découpe
         → coding/testing/merge → respond → supervise).
      3. Poll entry_result jusqu'à `supervised`, puis réponse au format LLM.
    """
    from AgentsCatalogue.lib.workspacedb import taskflow
    t0 = time.monotonic()
    # 1. entry (as_llm_leader, sans LLM)
    e = taskflow.create_entry({
        "workspace_id": WORKSPACE,
        "title": (prompt or "")[:80],
        "description": prompt or "",
        "entry_type": "chat_entry",
        "team_id": TEAM_ID,
    }, "")
    if not e.get("ok"):
        return {"ok": False, "error": e.get("error", "entry échouée")}
    task_id = e["task_id"]
    # 2. attendre la complétion (le swarm tourne en tâche de fond)
    status = ""
    while time.monotonic() - t0 < timeout_s:
        er = taskflow.entry_result({"workspace_id": WORKSPACE,
                                    "task_id": task_id}, "")
        status = er.get("task_status", "") or ""
        if status == "supervised":
            break
        time.sleep(5)
    if status != "supervised":
        return {"ok": False,
                "error": f"timeout (task {task_id}, statut {status})"}
    # 3. répondre au format LLM
    return _taskflow_reply(task_id, er.get("response"))


def _taskflow_reply(task_id: int, response: Any = None) -> Dict[str, Any]:
    """Réponse OpenAI depuis les livrables de la tâche (taskflow)."""
    try:
        from modules.sql.workspace import WorkspaceDB
        db = WorkspaceDB()
        sc = db.for_workspace(WORKSPACE)
        task = sc.tasks.get(int(task_id))
        reports = sc.tasks.get_reports(int(task_id)) or []
        db.close()
    except Exception:
        task, reports = None, []
    title = (task.get("title") or "") if task else ""
    parts = [f"### Résumé\n{title}"]
    for r in reports:
        role = r.get("role", "work")
        body = str(r.get("content", "") or "").strip()
        if body:
            parts.append(f"### {role}\n{body}")
    content = "\n\n".join(parts)
    return {
        "ok": True,
        "object": "chat.completion",
        "model": "mw-swarm",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0},
        "files": {}, "summary": title,
        "swarm": {"session": f"task_{task_id}",
                  "response": response,
                  "task_id": task_id},
    }
