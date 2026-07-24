"""Opérations git (dépôt central bare + clones par agent).

Migrées depuis services/skill_manager.py (_exec_*). Les helpers privés
(_central_repo, _agent_clone, _git_run, _git_identity, _clone_or_err,
_unmerged_files) sont reproduits à l'identique en fonctions module-level.
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import List, Optional, Tuple

from services.sandbox import Sandbox, SandboxError
from services._common import mw_home


def _central_repo(project_id: str) -> Path:
    return mw_home() / "repos" / f"{project_id}.git"


def _agent_clone(agent_id: str, project_id: str) -> Path:
    return (mw_home() / "memagent" / str(agent_id)
            / "workspace" / str(project_id))


def _git_run(root: Path, args: List[str], timeout: int = 60) -> dict:
    """Exécute `git -C {root} …` dans le working tree `root`.

    Normalise toujours la sortie : {stdout, stderr, exit_code, ok}.
    `ok` vaut True ssi exit_code == 0 — permet au FSM de détecter un
    échec git (commit/merge/push en erreur) au lieu de le laisser passer
    inaperçu (V0.6.23)."""
    if not Path(root).exists():
        return {"stdout": "", "stderr": "chemin inexistant", "exit_code": -1,
                "ok": False}
    try:
        stdout, stderr, rc = Sandbox().run(
            ["git", "-C", str(root)] + args, cwd=str(root),
            shell=False, timeout=timeout)
        return {"stdout": stdout, "stderr": stderr, "exit_code": rc,
                "ok": rc == 0}
    except SandboxError as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1, "ok": False}


def _git_identity(root: Path, agent_id: str) -> None:
    """Identité git locale : sans elle, `commit` échoue en env vierge."""
    who = str(agent_id) or "anon"
    _git_run(root, ["config", "user.email", f"{who}@modelweaver.local"])
    _git_run(root, ["config", "user.name", f"agent-{who}"])


def _clone_or_err(inputs: dict) -> Tuple[Optional[Path], Optional[dict]]:
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    if not pid or not aid:
        return None, {"stdout": "", "stderr": "project_id et agent_id requis",
                      "exit_code": -1, "ok": False}
    root = _agent_clone(aid, pid)
    if not (root / ".git").exists():
        return None, {"stdout": "", "stderr": "clone introuvable (git_clone ?)",
                      "exit_code": -1, "ok": False}
    return root, None


def _unmerged_files(root: Path) -> List[str]:
    """Liste des fichiers en conflit de merge (état non résolu)."""
    out = _git_run(root, ["diff", "--name-only", "--diff-filter=U"])
    return [l.strip() for l in out.get("stdout", "").splitlines() if l.strip()]


def repo_init(inputs: dict, ws: str) -> dict:
    """Crée le dépôt central BARE et y sème un commit initial (branche
    master : README + .gitignore)."""
    pid = inputs.get("project_id", "")
    if not pid:
        return {"ok": False, "error": "project_id requis"}
    bare = _central_repo(pid)
    if bare.exists():
        return {"ok": True, "path": str(bare), "note": "déjà initialisé"}
    bare.parent.mkdir(parents=True, exist_ok=True)
    sb = Sandbox()
    try:
        o, e, rc = sb.run(["git", "init", "--bare", "-b", "master", str(bare)],
                          shell=False, timeout=60)
        if rc != 0:
            o, e, rc = sb.run(["git", "init", "--bare", str(bare)],
                              shell=False, timeout=60)
        if rc != 0:
            return {"ok": False, "error": f"init bare: {e}"}
        tmp = Path(tempfile.mkdtemp(prefix="mw-seed-"))
        try:
            _git_run(tmp, ["init", "-q", "-b", "master"])
            _git_identity(tmp, "system")
            (tmp / "README.md").write_text(f"# Projet {pid}\n", encoding="utf-8")
            (tmp / ".gitignore").write_text("__pycache__/\n*.pyc\n",
                                            encoding="utf-8")
            _git_run(tmp, ["add", "-A"])
            _git_run(tmp, ["commit", "-q", "-m", "init"])
            _git_run(tmp, ["branch", "-M", "master"])
            _git_run(tmp, ["remote", "add", "origin", str(bare)])
            push = _git_run(tmp, ["push", "-q", "origin", "master"])
            if push["exit_code"] != 0:
                return {"ok": False, "error": f"seed push: {push['stderr']}"}
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
    except SandboxError as ex:
        return {"ok": False, "error": str(ex)}
    return {"ok": True, "path": str(bare)}


def git_clone(inputs: dict, ws: str) -> dict:
    """Clone le dépôt central dans le workspace perso de l'agent."""
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    if not pid or not aid:
        return {"ok": False, "error": "project_id et agent_id requis"}
    bare = _central_repo(pid)
    if not bare.exists():
        return {"ok": False, "error": "dépôt central inexistant (repo_init ?)"}
    dest = _agent_clone(aid, pid)
    if (dest / ".git").exists():
        r = _git_run(dest, ["fetch", "-q", "origin"])
        return {"ok": True, "path": str(dest), "note": "déjà cloné (fetch)",
                "exit_code": r["exit_code"]}
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        o, e, rc = Sandbox().run(["git", "clone", "-q", str(bare), str(dest)],
                                 shell=False, timeout=60)
    except SandboxError as ex:
        return {"ok": False, "error": str(ex)}
    if rc != 0:
        return {"ok": False, "error": f"clone: {e}"}
    _git_identity(dest, aid)
    return {"ok": True, "path": str(dest), "exit_code": rc}


def git_branch(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    name = inputs.get("name", "")
    create = inputs.get("create", False)
    if name and create:
        return _git_run(root, ["checkout", "-b", name])
    return _git_run(root, ["branch", "--list"])


def git_checkout(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    name = inputs.get("name", "")
    if not name:
        return {"stdout": "", "stderr": "name requis", "exit_code": -1}
    return _git_run(root, ["checkout", name])


def git_commit(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    msg = inputs.get("message", "update")
    _git_run(root, ["add", "-A"])
    return _git_run(root, ["commit", "-q", "-m", msg])


def git_diff(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    target = inputs.get("target", "")
    return _git_run(root, ["diff"] + ([target] if target else []))


def git_log(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    oneline = inputs.get("oneline", True)
    r = _git_run(root, ["log"] + (["--oneline"] if oneline else []))
    r["lines"] = [l for l in r["stdout"].splitlines()]
    return r


def git_status(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    r = _git_run(root, ["status", "--porcelain"])
    r["clean"] = (r["stdout"].strip() == "")
    r["conflicts"] = _unmerged_files(root)
    return r


def git_merge(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    if inputs.get("abort"):
        return _git_run(root, ["merge", "--abort"])
    name = inputs.get("name", "")
    if not name:
        return {"stdout": "", "stderr": "name requis", "exit_code": -1,
                "ok": False}
    args = ["merge", "--no-edit"]
    strat = inputs.get("strategy")
    if strat in ("ours", "theirs"):
        args += ["-X", strat]
    args.append(name)
    r = _git_run(root, args)
    if r["exit_code"] != 0:
        # Merge en échec : survolontairement un conflit de contenu.
        r = dict(r)
        r["conflict"] = ("CONFLICT" in r.get("stderr", "")
                         or "CONFLICT" in r.get("stdout", ""))
        r["conflicts"] = _unmerged_files(root)
        r["error"] = ("merge en échec (conflit de contenu)" if r["conflict"]
                      else f"merge en échec: {r.get('stderr', '')[:200]}")
    return r


def git_add(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    path = inputs.get("path", "")
    if path:
        return _git_run(root, ["add", "--", path])
    return _git_run(root, ["add", "-A"])


def git_resolve_conflict(inputs: dict, ws: str) -> dict:
    """Résout un conflit de merge en choisissant un côté (ours/theirs)
    puis stage le(s) fichier(s) — à faire après un git_merge en conflit
    avant le commit de conclusion.

    `path` :
      - "all"  : résout tous les fichiers en conflit du clone ;
      - sinon  : fichier unique (relatif au clone)."""
    root, err = _clone_or_err(inputs)
    if err:
        return err
    side = inputs.get("side", "ours")
    if side not in ("ours", "theirs"):
        return {"ok": False, "error": "side doit être 'ours' ou 'theirs'"}
    path = inputs.get("path", "")
    if path == "all":
        files = _unmerged_files(root)
        if not files:
            return {"ok": True, "resolved": [], "note": "aucun conflit"}
        resolved = []
        for f in files:
            co = _git_run(root, ["checkout", f"--{side}", "--", f])
            if co["exit_code"] != 0:
                return co
            ad = _git_run(root, ["add", "--", f])
            if ad["exit_code"] != 0:
                return ad
            resolved.append(f)
        return {"ok": True, "resolved": resolved}
    if not path:
        return {"ok": False, "error": "path requis (ou 'all')",
                "exit_code": -1}
    co = _git_run(root, ["checkout", f"--{side}", "--", path])
    if co["exit_code"] != 0:
        return co
    return _git_run(root, ["add", "--", path])


def git_fetch(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    return _git_run(root, ["fetch", "-q", "origin"])


def git_pull(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    branch = inputs.get("branch", "") or inputs.get("name", "")
    if branch:
        return _git_run(root, ["pull", "--no-edit", "origin", branch])
    return _git_run(root, ["pull", "--no-edit"])


def git_push(inputs: dict, ws: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    branch = inputs.get("branch", "")
    ref = branch if branch else "HEAD"
    return _git_run(root, ["push", "-u", "origin", ref])


__skills__ = [
    "repo_init", "git_clone", "git_branch", "git_checkout", "git_commit",
    "git_diff", "git_log", "git_status", "git_merge", "git_add",
    "git_resolve_conflict", "git_fetch", "git_pull", "git_push",
]
