"""git-lite — interface git légère pour agents LLM.

Architecture distribuée :
  - origin : dépôt central local (serveur Team Leader)
  - upstream : dépôt distant réel (GitHub/GitLab)
  - agents : push/pull sur origin uniquement
  - Team Leader : gère les PRs + sync upstream

Format de sortie concis (minimise les tokens LLM).
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional


# ── chemins ───────────────────────────────────────────────────

def _central_repo(home: str) -> Path:
    """Dépôt central local = shared/repo.git dans le home."""
    return Path(home) / "shared" / "repo.git"


def _prs_file(home: str) -> Path:
    return _central_repo(home).parent / "prs.json"


def _agent_workspace(home: str, agent_id: str) -> Path:
    return Path(home) / "agents" / agent_id / "workspace"


# ── git helpers ────────────────────────────────────────────────

def _git(cmd: List[str], cwd: str, timeout: int = 30) -> Dict:
    try:
        proc = subprocess.run(
            ["git"] + cmd,
            capture_output=True, text=True, timeout=timeout,
            cwd=cwd,
        )
        return {
            "exit_code": proc.returncode,
            "stdout": proc.stdout,
            "stderr": proc.stderr,
        }
    except FileNotFoundError:
        return {"exit_code": 127, "stdout": "", "stderr": "git not found"}
    except subprocess.TimeoutExpired:
        return {"exit_code": 124, "stdout": "", "stderr": "git timeout"}


def _git_concise(cmd: List[str], cwd: str, timeout: int = 30) -> str:
    r = _git(cmd, cwd, timeout)
    if r["exit_code"] != 0:
        return f"ERR: {r['stderr'][:200]}"
    return r["stdout"].strip()


# ── autorisation ──────────────────────────────────────────────

def _check_auth(action: str, agent_id: str, role: str, inputs: Dict) -> Optional[Dict]:
    """Vérifie les droits. Retourne None si OK, un dict d'erreur si refusé."""
    # Read-only : tout le monde
    if action in ("status", "diff", "log", "branch", "remote"):
        return None
    # Write : agent standard
    if action in ("add", "commit", "checkout", "pull", "init", "stash"):
        return None
    # Push, PR : agent peut push sa branche, PR nécessite message
    if action == "push":
        return None  # on autorise le push de branche
    if action == "pr-create":
        return None  # tout agent peut créer une PR
    # Team Leader only
    if action in ("pr-list", "pr-accept", "pr-reject", "cloud-sync", "cloud-init"):
        if role != "leader":
            return {"exit_code": 1, "stdout": "", "stderr": f"action '{action}' réservée au team_leader", "status": "denied"}
        return None
    return {"exit_code": 1, "stdout": "", "stderr": f"action inconnue : {action}"}


# ── commandes ─────────────────────────────────────────────────

def _cmd_init(inputs: Dict, home: str, agent_id: str, role: str) -> Dict:
    """Initialise le dépôt central local et clone dans l'espace de l'agent."""
    central = _central_repo(home)
    ws = _agent_workspace(home, agent_id)

    # Créer le dépôt central s'il n'existe pas
    if not central.exists():
        central.parent.mkdir(parents=True, exist_ok=True)
        r = _git(["init", "--bare", str(central)], cwd=str(central.parent))
        if r["exit_code"] != 0:
            return {"exit_code": 1, "stdout": "", "stderr": f"init central: {r['stderr']}"}
        # Premier commit dans main
        tmp = central.parent / "_tmp_init"
        tmp.mkdir(exist_ok=True)
        (tmp / "README.md").write_text(f"# Project\n\nInitialized by git-lite\n")
        _git(["init"], cwd=str(tmp))
        _git(["add", "."], cwd=str(tmp))
        _git(["commit", "-m", "initial commit"], cwd=str(tmp))
        _git(["branch", "-M", "main"], cwd=str(tmp))
        _git(["remote", "add", "origin", str(central)], cwd=str(tmp))
        _git(["push", "-u", "origin", "main"], cwd=str(tmp))
        import shutil
        shutil.rmtree(tmp)

    # Cloner dans l'espace de l'agent si pas déjà fait
    if not (ws / ".git").exists():
        ws.parent.mkdir(parents=True, exist_ok=True)
        r = _git(["clone", str(central), str(ws)], cwd=str(ws.parent))
        if r["exit_code"] != 0:
            return {"exit_code": 1, "stdout": "", "stderr": f"clone agent: {r['stderr']}"}

    return {"exit_code": 0, "stdout": f"repo prêt dans {ws}"}


def _cmd_status(inputs: Dict, cwd: str) -> str:
    r = _git_concise(["status", "--porcelain"], cwd)
    if not r:
        return "clean"
    return r


def _cmd_diff(inputs: Dict, cwd: str) -> str:
    staged = inputs.get("staged", False)
    args = ["diff", "--cached"] if staged else ["diff", "--stat"]
    r = _git_concise(args, cwd)
    if not r:
        return "no changes"
    return r[:500]  # tronqué pour token economy


def _cmd_log(inputs: Dict, cwd: str) -> str:
    n = min(inputs.get("n", 5), 20)
    fmt = inputs.get("format", "oneline")
    if fmt == "oneline":
        r = _git_concise(["log", f"-{n}", "--oneline", "--decorate"], cwd)
    else:
        r = _git_concise(["log", f"-{n}", "--oneline", "--decorate"], cwd)
    return r or "no commits"


def _cmd_branch(inputs: Dict, cwd: str) -> str:
    all_b = inputs.get("all", False)
    args = ["branch"] if not all_b else ["branch", "-a"]
    r = _git_concise(args, cwd)
    return r or "no branches"


def _cmd_add(inputs: Dict, cwd: str) -> str:
    paths = inputs.get("paths", ["."])
    args = ["add"] + paths
    r = _git_concise(args, cwd)
    if r.startswith("ERR"):
        return r
    return f"staged: {paths}"


def _cmd_commit(inputs: Dict, cwd: str) -> str:
    msg = inputs.get("message", "")
    if not msg:
        return "ERR: message required"
    r = _git_concise(["commit", "-m", msg], cwd)
    if r.startswith("ERR"):
        return r
    return f"committed: {msg[:60]}"


def _cmd_pull(inputs: Dict, cwd: str) -> str:
    branch = inputs.get("branch", "main")
    r = _git_concise(["pull", "origin", branch, "--rebase"], cwd)
    if r.startswith("ERR"):
        return r
    if not r:
        return f"up-to-date with origin/{branch}"
    return r[:200]


def _cmd_push(inputs: Dict, cwd: str) -> str:
    branch = inputs.get("branch", "HEAD")
    args = ["push", "origin", branch]
    if inputs.get("force"):
        args.append("--force")
    r = _git_concise(args, cwd)
    if r.startswith("ERR"):
        return r[:300]
    return f"pushed {branch} to origin"


def _cmd_pr_create(inputs: Dict, cwd: str, agent_id: str) -> str:
    branch = inputs.get("branch", "")
    if not branch:
        return "ERR: branch required"
    title = inputs.get("title", branch)
    prs = []
    prf = _prs_file(inputs.get("home", ""))
    if prf.exists():
        try:
            prs = json.loads(prf.read_text())
        except (json.JSONDecodeError, OSError):
            prs = []
    pr_id = len(prs) + 1
    prs.append({
        "id": pr_id,
        "branch": branch,
        "agent": agent_id,
        "title": title,
        "status": "open",
    })
    prf.parent.mkdir(parents=True, exist_ok=True)
    prf.write_text(json.dumps(prs, indent=2))
    return f"PR #{pr_id} created: {title} (branch: {branch})"


def _cmd_pr_list(inputs: Dict, home: str) -> str:
    prf = _prs_file(home)
    if not prf.exists():
        return "no PRs"
    try:
        prs = json.loads(prf.read_text())
    except (json.JSONDecodeError, OSError):
        return "no PRs"
    open_prs = [p for p in prs if p.get("status") == "open"]
    if not open_prs:
        return "no open PRs"
    lines = []
    for p in open_prs:
        lines.append(f"  #{p['id']} {p['branch']} by {p.get('agent','?')}: {p.get('title','')}")
    return "Open PRs:\n" + "\n".join(lines)


def _cmd_pr_accept(inputs: Dict, cwd: str, home: str) -> str:
    branch = inputs.get("branch", "")
    if not branch:
        return "ERR: branch required"
    # Merge dans main local
    r1 = _git_concise(["checkout", "main"], cwd)
    if r1.startswith("ERR"):
        return r1
    r2 = _git_concise(["pull", "origin", "main"], cwd)
    r3 = _git_concise(["merge", "--no-ff", f"origin/{branch}", "-m", f"merge PR: {branch}"], cwd)
    if r3.startswith("ERR"):
        return r3
    r4 = _git_concise(["push", "origin", "main"], cwd)
    # Nettoyer la branche distante
    _git_concise(["push", "origin", "--delete", branch], cwd)
    # Marquer la PR comme acceptée
    _update_pr_status(home, branch, "accepted")
    return f"PR {branch} merged into main"


def _cmd_pr_reject(inputs: Dict, cwd: str, agent_id: str, home: str) -> str:
    branch = inputs.get("branch", "")
    if not branch:
        return "ERR: branch required"
    _update_pr_status(home, branch, "rejected")
    return f"PR {branch} rejected"


def _update_pr_status(home: str, branch: str, status: str) -> None:
    prf = _prs_file(home)
    if not prf.exists():
        return
    try:
        prs = json.loads(prf.read_text())
        updated = False
        for p in prs:
            if p.get("branch") == branch and p.get("status") == "open":
                p["status"] = status
                updated = True
                break
        if updated:
            prf.write_text(json.dumps(prs, indent=2))
    except (json.JSONDecodeError, OSError):
        pass


def _cmd_cloud_sync(inputs: Dict, cwd: str) -> str:
    """Team Leader : sync upstream vers origin."""
    upstream_url = inputs.get("url", "")
    if not upstream_url:
        # Vérifier si upstream existe déjà
        r = _git_concise(["remote", "-v"], cwd)
        if "upstream" not in r:
            return "ERR: no upstream remote configured (use cloud-init)"
    # Pull upstream
    r1 = _git_concise(["checkout", "main"], cwd)
    if r1.startswith("ERR"):
        return r1
    r2 = _git_concise(["pull", "upstream", "main"], cwd)
    if r2.startswith("ERR"):
        return r2
    # Push vers origin local
    r3 = _git_concise(["push", "origin", "main"], cwd)
    if r3.startswith("ERR"):
        return r3
    return f"synced with upstream"


def _cmd_cloud_init(inputs: Dict, cwd: str) -> str:
    url = inputs.get("url", "")
    if not url:
        return "ERR: url required"
    central = _central_repo(inputs.get("home", ""))
    # Cloner central dans un temp, ajouter upstream, pusher
    tmp = central.parent / "_tmp_upstream"
    tmp.mkdir(exist_ok=True)
    _git(["clone", str(central), str(tmp)], cwd=str(central.parent))
    r1 = _git_concise(["remote", "add", "upstream", url], cwd=str(tmp))
    if r1.startswith("ERR"):
        import shutil
        shutil.rmtree(tmp)
        return r1
    r2 = _git_concise(["push", "-u", "upstream", "main"], cwd=str(tmp))
    import shutil
    shutil.rmtree(tmp)
    if r2.startswith("ERR"):
        return r2
    return f"upstream configured: {url}"


# ── entrée principale ─────────────────────────────────────────

ACTION_MAP = {
    "init": _cmd_init,
    "status": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_status(i, str(_agent_workspace(h, a))), "stderr": ""},
    "diff": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_diff(i, str(_agent_workspace(h, a))), "stderr": ""},
    "log": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_log(i, str(_agent_workspace(h, a))), "stderr": ""},
    "branch": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_branch(i, str(_agent_workspace(h, a))), "stderr": ""},
    "add": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_add(i, str(_agent_workspace(h, a))), "stderr": ""},
    "commit": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_commit(i, str(_agent_workspace(h, a))), "stderr": ""},
    "pull": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_pull(i, str(_agent_workspace(h, a))), "stderr": ""},
    "push": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_push(i, str(_agent_workspace(h, a))), "stderr": ""},
    "pr-create": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_pr_create(i, str(_agent_workspace(h, a)), a), "stderr": ""},
    "pr-list": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_pr_list(i, h), "stderr": ""},
    "pr-accept": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_pr_accept(i, str(_central_repo(h).parent / a / "workspace"), h), "stderr": ""},
    "pr-reject": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_pr_reject(i, str(_agent_workspace(h, a)), a, h), "stderr": ""},
    "cloud-sync": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_cloud_sync(i, str(_central_repo(h).parent)), "stderr": ""},
    "cloud-init": lambda i, h, a, r: {"exit_code": 0, "stdout": _cmd_cloud_init(i, str(_central_repo(h).parent)), "stderr": ""},
}


def exec(inputs: dict, home: str) -> dict:
    action = inputs.get("action", "")
    if not action:
        return {"exit_code": 1, "stdout": "", "stderr": "action required"}

    agent_id = Path(home).name
    role = inputs.get("role", "member")

    # Vérifier autorisation
    auth = _check_auth(action, agent_id, role, inputs)
    if auth is not None:
        return auth

    # Exécuter l'action
    handler = ACTION_MAP.get(action)
    if handler is None:
        return {"exit_code": 1, "stdout": "", "stderr": f"action inconnue : {action}"}

    result = handler(inputs, home, agent_id, role)
    return result


__skills__ = ["exec"]