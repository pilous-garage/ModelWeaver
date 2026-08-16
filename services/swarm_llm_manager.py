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

ANALYST_AGENT = "team:llm-code/decoupeur"
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

def _log_exchange(kind: str, payload: Dict[str, Any]) -> None:
    """Journalise un échange swarm-as-llm (requête benchmark → réponse).

    Append-only JSONL dans ~/.modelweaver/logs/swarm_exchange.jsonl : on y
    stocke le prompt reçu, la réponse renvoyée (ou l'erreur) et le task_id,
    pour pouvoir debugger le format exact servi au benchmark (inspect_ai).
    Best-effort : ne lève jamais."""
    try:
        import json as _json
        log_path = MW_HOME / "logs"
        log_path.mkdir(parents=True, exist_ok=True)
        with open(log_path / "swarm_exchange.jsonl", "a",
                  encoding="utf-8") as f:
            f.write(_json.dumps({"ts": int(time.time()),
                                 "kind": kind, **payload},
                                ensure_ascii=False) + "\n")
    except Exception:
        pass


def run_completion(prompt: str, files: Optional[Dict[str, str]] = None,
                   timeout_s: int = 1800) -> Dict[str, Any]:
    """Exécute le flux complet d'un /v1/chat/completions — TASKFLOW V0.15.

    Retourne une réponse OpenAI {choices, files, summary} ou une erreur.

    Nouveau flux (sans LLM côté entrée) :
      1. as_llm_leader → create_entry : task `chat_entry` + sub_task analysis.
      2. Le taskflow tourne automatiquement (amorce → greedy : analyste découpe
         → coding/testing/merge → respond → supervise).
      3. Poll entry_result jusqu'à `supervised`, puis réponse au format LLM.
    """
    from AgentsCatalogue.lib.workspacedb import taskflow
    from services import swarm_repo
    t0 = time.monotonic()
    # 1. entry (as_llm_leader, sans LLM)
    _log_exchange("request", {"prompt": (prompt or "")[:2000],
                              "files": list((files or {}).keys())})
    # repo global + branche par requête : le swarm produit les fichiers sur
    # cette branche ; _taskflow_reply renverra prompt + git diff(first..HEAD).
    requete_id = f"req-{uuid.uuid4().hex[:10]}"
    swarm_repo.ensure()
    br = swarm_repo.create_branch(requete_id)
    if not br.get("ok"):
        _log_exchange("entry_error", {"error": f"create_branch: {br.get('error')}"})
        return {"ok": False, "error": f"create_branch: {br.get('error')}"}
    branch = br["branch"]
    # dépose prompt + fichiers fournis sur la branche (commit seed de la requête)
    swarm_repo.write_seed(branch, prompt, files or {})
    e = taskflow.create_entry({
        "workspace_id": WORKSPACE,
        "title": (prompt or "")[:80],
        "description": prompt or "",
        # PREMIÈRE ÉTAPE : entry_type ABSENT → create_entry classifie la requête
        # (prompt épurée → consensus) en simple/texte/code → route
        # chat_entry/completion_entry/feature. Le swarm adapte son pipeline.
        "team_id": TEAM_ID,
        "repo": swarm_repo.GLOBAL_REPO_NAME,
        "branch": branch,
    }, "")
    if not e.get("ok"):
        err = e.get("error", "entry échouée")
        _log_exchange("entry_error", {"error": err, "prompt": (prompt or "")[:500]})
        return {"ok": False, "error": err}
    task_id = e["task_id"]
    _log_exchange("entry_created", {"task_id": task_id,
                                    "branch": branch,
                                    "requete_id": requete_id})
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
        err = f"timeout (task {task_id}, statut {status})"
        _log_exchange("timeout", {"task_id": task_id, "status": status})
        return {"ok": False, "error": err}
    # 3. répondre au format LLM (prompt originelle + git diff du travail produit)
    reply = _taskflow_reply(task_id, er.get("response"),
                            prompt=prompt, requete_id=requete_id)
    _log_exchange("reply", {"task_id": task_id,
                            "reply_ok": reply.get("ok"),
                            "reply": reply})
    return reply


def _taskflow_reply(task_id: int, response: Any = None,
                    prompt: str = "", requete_id: str = "") -> Dict[str, Any]:
    """Réponse OpenAI depuis les livrables de la tâche (taskflow).

    Priorité :
      1. Le `response` du respond (la vraie réponse texte produite — c'est le
         livrable pour les tâches simple/texte, classifiées par le consensus).
      2. Sinon la PROMPT + le `git diff` du travail produit (tâches code).
      3. Sinon les rapports (repli).
    """
    # 1. La réponse du respond (sub_task respond) : contenu texte réel.
    resp_text = ""
    if isinstance(response, str) and response.strip():
        resp_text = response.strip()
    elif isinstance(response, dict):
        resp_text = str(response.get("content") or response.get("text") or "")
    if not resp_text:
        # chercher dans les rapports de la task (role=respond)
        try:
            from modules.sql.workspace import WorkspaceDB
            db = WorkspaceDB()
            sc = db.for_workspace(WORKSPACE)
            for r in (sc.tasks.get_reports(int(task_id)) or []):
                if (r.get("role") == "respond" and r.get("content")):
                    resp_text = str(r["content"]).strip()
                    break
            db.close()
        except Exception:
            pass
    if resp_text:
        return {
            "ok": True,
            "object": "chat.completion",
            "model": "mw-swarm",
            "choices": [{"index": 0,
                         "message": {"role": "assistant", "content": resp_text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0},
            "files": {},
            "summary": (prompt or "")[:80],
            "swarm": {"session": f"task_{task_id}",
                      "response": response,
                      "task_id": task_id,
                      "requete_id": requete_id},
        }
    # 2. Travail produit (code) : PROMPT + git diff.
    try:
        from services import swarm_repo
        d = swarm_repo.diff_for(requete_id) if requete_id else {}
    except Exception:
        d = {}
    if d.get("ok") and d.get("diff"):
        content = f"### PROMPT\n{prompt}\n\n### DIFF (travail produit)\n{d['diff']}"
        files = {f: None for f in d.get("files", [])}
    else:
        # repli : rapports (ancien comportement)
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
        files = {}
    return {
        "ok": True,
        "object": "chat.completion",
        "model": "mw-swarm",
        "choices": [{"index": 0,
                     "message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0,
                  "total_tokens": 0},
        "files": files,
        "summary": (prompt or "")[:80],
        "swarm": {"session": f"task_{task_id}",
                  "response": response,
                  "task_id": task_id,
                  "requete_id": requete_id},
    }


def run_proxy_completion(prompt: str, model: str = "proxy_llm_fallback",
                         use_case: str = "chat",
                         restrict_llm: Optional[list] = None,
                         tools: Optional[list] = None,
                         tool_choice: Any = None,
                         messages: Optional[list] = None) -> Dict[str, Any]:
    """Réponse OpenAI DIRECTE via le proxy (ask_llm_autofallback, sans swarm).

    Le proxy simule un endpoint LLM unique : un SEUL appel LLM réussi par
    prompt (sélection du modèle via ask_llm_autofallback → bridge → réponse
    formatée /chat/completions). Retry SEULEMENT sur erreur (re-alloc autre
    modèle, excluant le défaillant) — PAS de boucle.

    `tools`/`tool_choice` (schémas OpenAI, optionnels) : si fournis, le LLM
    peut répondre par des tool_calls — ils sont transmis au bridge et renvoyés
    tels quels au format OpenAI (les benchmarks d'agents, ex. SWE-bench, en
    dépendent). `messages` : historique complet éventuel (le dernier user
    message sert de prompt).

    TOUT est loggé (prompt, allocation, réponse) :
      - {PROXY_HOME}/ask_llm_autofallback.log (par le wrapper)
      - ~/.modelweaver/logs/swarm_exchange.jsonl (kind=proxy_*)

    DIAGNOSTIC benchmark : si le proxy répond correctement mais le benchmark
    est à 0, le problème est la CONCEPTION prompt/réponse (format attendu vs
    produit). Si le proxy échoue, c'est l'allocation/ask_llm.
    """
    t0 = time.monotonic()
    _log_exchange("proxy_request", {"prompt": (prompt or "")[:2000],
                                    "model": model, "use_case": use_case,
                                    "has_tools": bool(tools),
                                    "first_tool": (tools or [None])[0]})
    try:
        from services.skill_manager import call_skill
        # ask_llm_autofallback : sélection (LLM fourni ou ask_llm) + UN appel
        # bridge ; échec → fallback autre modèle. Retourne llm_used + response.
        r = call_skill("ask_llm_autofallback", {
            "prompt": prompt, "use_case": use_case,
            "type_endpoint": "chat",
            # Par défaut : modèles RAPIDES (nvidia est lent, ~40s/réponse).
            # Surchargeable via restrict_llm (paramètre).
            "restrict_llm": restrict_llm or _PROXY_FAST_MODELS,
            "max_essais": 3, "timeout": 90,
            "tools": tools, "tool_choice": tool_choice,
        }, home=PROXY_HOME)
        if not r.get("ok") or (not r.get("response") and not r.get("tool_calls")):
            err = r.get("error", "réponse vide")
            _log_exchange("proxy_call_error", {"error": err})
            return {"ok": False, "error": err}
        content = r.get("response") or ""
        tool_calls = r.get("tool_calls")
        llm_used = r.get("llm_used") or {}
        p_ref = r.get("provider_ref") or llm_used.get("provider_ref", "")
        m_ref = r.get("model_ref") or llm_used.get("model_ref", "")
        # format OpenAI.
        message: Dict[str, Any] = {"role": "assistant", "content": content}
        if tool_calls:
            message["tool_calls"] = tool_calls
        resp = {
            "ok": True,
            "object": "chat.completion",
            "model": model or "proxy_llm_fallback",
            "choices": [{"index": 0,
                         "message": message,
                         "finish_reason": "tool_calls" if tool_calls else "stop"}],
            "usage": {"prompt_tokens": 0,
                      "completion_tokens": max(1, len(content) // 4),
                      "total_tokens": max(1, len(content) // 4)},
            "proxy": {"provider": p_ref, "model_real": m_ref,
                      "id_address": llm_used.get("id_address", -1),
                      "fallback": r.get("fallback", False),
                      "duration_ms": int((time.monotonic() - t0) * 1000)},
        }
        _log_exchange("proxy_response", {"content": content[:2000],
                                         "model_real": m_ref})
        # Log local {PROXY_HOME}/proxy_llm.log (prompt → allocation → réponse).
        try:
            from pathlib import Path
            _p = Path(PROXY_HOME) / "proxy_llm.log"
            _p.parent.mkdir(parents=True, exist_ok=True)
            with open(_p, "a", encoding="utf-8") as _fh:
                _fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                          f"prompt: {prompt[:500]}\n")
                _fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                          f"alloc: {p_ref}/{m_ref}\n")
                _fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] "
                          f"response: {content[:500]}\n")
        except Exception:
            pass
        return resp
    except Exception as e:  # noqa: BLE001
        _log_exchange("proxy_error", {"error": str(e)})
        return {"ok": False, "error": str(e)}


# Home du proxy (log dédié {home}/proxy_llm.log). Un répertoire stable permet
# de retrouver les logs du proxy par rapport aux runs des agents.
PROXY_HOME = str(MW_HOME / "agent_home" / "proxy_llm_fallback")


# Modèles RAPIDES par défaut pour le proxy (nvidia est lent, ~40s/réponse ;
# groq/opencode-zen répondent en ~5s). Surchargeable via restrict_llm.
# Liste ÉLARGIE : certains passent en repos (rate-limit/unavailable) après des
# échecs → le fallback doit avoir de la marge.
_PROXY_FAST_MODELS = [
    "groq/llama-3.3-70b-versatile",
    "opencode-zen/mimo-v2.5-free",
    "opencode-zen/deepseek-v4-flash-free",
    "groq/llama-3.1-8b-instant",
    "opencode-zen/deepseek-v4-flash",
    "opencode-zen/claude-sonnet-4-6",
    "nvidia/meta/llama-3.3-70b-instruct",
]
