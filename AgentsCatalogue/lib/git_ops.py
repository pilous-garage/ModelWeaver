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


# ── Aide des outils (seedée dans le repo central par repo_init) ──
# Détaillé aux agents : les tools dispo, leur rôle, et l'ordre d'utilisation.
_TOOLS_HELP_MD = """# Outils disponibles (aide)

Ce dépôt a été initialisé par ModelWeaver. Voici les outils que tu peux
utiliser pour travailler, et dans quel ordre.

## Git (dépôt central)

Le dépôt central est un repo BARE local (`~/.modelweaver/repos/{id}.git`).
Chaque agent travaille dans un CLONE personnel (son workspace).

Séquence normale d'un agent :
1. `git/repo_list@v1` (project_id) — vérifier que le dépôt central existe
   (champ `present`). S'il n'existe pas :
2. `git/repo_init@v1` (project_id) — crée le dépôt central + commit initial
   (README.md, .gitignore, tools_help.md). Idempotent.
3. `git/git_clone@v1` (project_id, agent_id) — clone le dépôt dans ton
   workspace perso. Idempotent (fetch si déjà cloné).
4. `git/git_status@v1` — voir l'état de tes changements.
5. `git/git_add@v1` (path) — stage un fichier (ou tout).
6. `git/git_commit@v1` (message) — committe.
7. `git/git_push@v1` (branch) — pousse vers le dépôt central. Fait un
   pull --rebase avant (les autres membres peuvent avoir avancé).

Autres outils git : git_branch (créer/lister), git_checkout (changer de
branche), git_log (historique), git_diff (diff), git_pull (rebase depuis
central), git_merge (fusionner une branche), git_resolve_conflict
(résoudre un conflit de merge, side=ours/theirs), git_fetch,
git_push_remote (push vers un remote distant, leader).

## Fichiers (ton workspace = clone git)

- `file/read_file@v1`, `file/write_file@v1`, `file/append_file@v1`
- `file/list_dir@v1`, `file/glob@v1`, `file/grep@v1`
- `system/home/*` : les mêmes opérations sur ton home agent (hors git).

## Shell

- `shell/exec@v1` : exécute une commande (si autorisé).

## Workspace / tâches

- `workspace/task_list@v1`, `workspace/task_get@v1`
- `workspace/task_claim_next@v1` : piocher la tâche suivante de ton rôle.
- `workspace/task_done@v1` : marquer ta tâche terminée (avec livrable).
- `workspace/task_create@v1` : créer une sous-tâche.

## Règles d'or

- Ne génère jamais de code dans ta réponse : appelle directement les outils.
- Vérifie toujours `repo_list` avant de cloner (si le repo manque → `repo_init`).
- Committe PUIS pousse. Le merge final est fait par l'intégrateur.
"""


def _agent_clone(agent_id: str, project_id: str) -> Path:
    return (mw_home() / "agent_home" / str(agent_id)
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
    # Retry sur les erreurs de LOCK git (index.lock concurrent) : des runs
    # peuvent se chevaucher sur le clone (reviewer re-pick) → git échoue
    # « Unable to create index ». On attend et on re-tente quelques fois.
    import time as _time
    for attempt in range(4):
        try:
            stdout, stderr, rc = Sandbox().run(
                ["git", "-C", str(root)] + args, cwd=str(root),
                shell=False, timeout=timeout)
            if rc == 0 or "index.lock" not in stderr:
                return {"stdout": stdout, "stderr": stderr, "exit_code": rc,
                        "ok": rc == 0}
            # lock conflict → attendre et re-tenter
            _time.sleep(0.5 * (attempt + 1))
        except SandboxError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1, "ok": False}
    # Lock persistant (résidu de process mort probable) → supprimer le lock
    # obsolète et re-tenter UNE dernière fois.
    try:
        lock = Path(root) / ".git" / "index.lock"
        if lock.exists():
            lock.unlink(missing_ok=True)
        stdout, stderr, rc = Sandbox().run(
            ["git", "-C", str(root)] + args, cwd=str(root),
            shell=False, timeout=timeout)
        return {"stdout": stdout, "stderr": stderr, "exit_code": rc,
                "ok": rc == 0}
    except Exception:
        pass
    return {"stdout": "", "stderr": "git lock persistant (index.lock)",
            "exit_code": -1, "ok": False}


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


def repo_init(inputs: dict, home: str) -> dict:
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
            (tmp / "tools_help.md").write_text(_TOOLS_HELP_MD, encoding="utf-8")
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


def repo_list(inputs: dict, home: str) -> dict:
    """Liste les dépôts centraux BARE locaux (~/.modelweaver/repos/*.git).

    Sans paramètre : liste tous les repos centraux existants.
    Avec `project_id` : vérifie qu'un repo précis existe (ok=True si présent).
    Retourne repos = [{id, path, size_mb, last_commit}] triés par activité.
    """
    repos_dir = mw_home() / "repos"
    if not repos_dir.is_dir():
        return {"ok": True, "repos": [], "count": 0}
    pid = inputs.get("project_id", "")
    out = []
    for bare in sorted(repos_dir.glob("*.git")):
        rid = bare.name[:-4]
        if pid and rid != pid:
            continue
        entry = {"id": rid, "path": str(bare)}
        try:
            size = sum(f.stat().st_size for f in bare.rglob("*") if f.is_file())
            entry["size_mb"] = round(size / (1024 * 1024), 1)
        except Exception:
            entry["size_mb"] = 0.0
        # Dernier commit (HEAD) — best-effort sur le bare.
        try:
            o, e, rc = Sandbox().run(
                ["git", "--git-dir", str(bare), "log", "-1",
                 "--format=%h %ad %s", "--date=short"],
                shell=False, timeout=30)
            entry["last_commit"] = o.strip() if rc == 0 and o.strip() else ""
        except Exception:
            entry["last_commit"] = ""
        out.append(entry)
    if pid:
        present = any(e["id"] == pid for e in out)
        return {"ok": True, "present": present, "repos": out, "count": len(out)}
    return {"ok": True, "repos": out, "count": len(out)}


def git_clone(inputs: dict, home: str) -> dict:
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
        # CLEAN du working tree : un run précédent a pu laisser des fichiers
        # non commités (write_file hors git, bench/, work/…) qui pollueraient
        # le prochain travail et casseraient la vérif git (faux diffs).
        # On repart d'un état PROPRE au HEAD du clone, sans toucher aux
        # commits.
        _git_run(dest, ["reset", "-q", "--hard", "HEAD"])
        _git_run(dest, ["clean", "-fdq"])
        return {"ok": True, "path": str(dest), "note": "déjà cloné (fetch+clean)",
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


def git_branch(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    name = inputs.get("name", "")
    create = inputs.get("create", False)
    if name and create:
        return _git_run(root, ["checkout", "-b", name])
    return _git_run(root, ["branch", "--list"])


def git_checkout(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    name = inputs.get("name", "")
    # Nom vide/placeholder non résolu → rester sur la branche par défaut du
    # clone (master). Une tâche sans branche/commit cible ne doit pas échouer.
    if not name or "{{" in str(name):
        cur = _git_run(root, ["branch", "--show-current"])
        b = cur.get("stdout", "").strip() or "master"
        return {"ok": True, "stdout": f"aucune branche cible — reste sur {b}",
                "branch": b, "exit_code": 0}
    # Accepte branche, branche distante (origin/x) et SHA de commit (detached HEAD).
    r = _git_run(root, ["checkout", name])
    if r["exit_code"] != 0:
        # Branche absente localement → tenter depuis la distante (si le repo
        # distant est disponible), sinon laisser l'erreur remonter.
        r2 = _git_run(root, ["checkout", "-B", name, f"origin/{name}"])
        if r2["exit_code"] == 0:
            return r2
    return r


def git_commit(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    msg = inputs.get("message", "update")
    _git_add_safe(root)
    return _git_run(root, ["commit", "-q", "-m", msg])


def _git_add_safe(root: Path) -> None:
    """git add -A protégé contre les suppressions accidentelles de masse.

    Un agent qui committe ne doit pas supprimer des dizaines de fichiers
    existants par accident (working tree incomplet, clone partiel, tool
    destructeur). On stagie tout, puis on restaure depuis HEAD les fichiers
    supprimés au-delà d'un petit seuil.
    """
    _git_run(root, ["add", "-A"])
    st = _git_run(root, ["diff", "--cached", "--name-status"])
    deleted = []
    for line in (st.get("stdout") or "").splitlines():
        if line.startswith("D"):
            parts = line.split("\t")
            deleted.append(parts[-1].strip() if parts else "")
    deleted = [d for d in deleted if d]
    if not deleted or len(deleted) <= 2:
        return
    for f in deleted:
        _git_run(root, ["checkout", "-q", "HEAD", "--", f])
        _git_run(root, ["reset", "-q", "HEAD", "--", f])


def git_diff(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    target = inputs.get("target", "")
    return _git_run(root, ["diff"] + ([target] if target else []))


def git_review_diff(inputs: dict, home: str) -> dict:
    """git_review_diff — DIFF d'une plage de commits d'une tâche (MÉCANIQUE).

    Plage = `commit_start..commit_end` :
      - commit_start : point de départ (partagé en cas de split A→B,C) ;
      - commit_end   : livrable (commit_current / commit_hash).
    Si commit_start est vide, on diff vs le PARENT de commit_end (le travail
    du dernier commit). Retourne {diff, files, stat} pour que le reviewer
    examine le livrable SANS outils git LLM.

    NB : on lit le diff depuis le DÉPÔT CENTRAL BARE (lecture pure), PAS le
    clone de l'agent — évite les locks index.lock concurrents quand plusieurs
    runs partagent le clone (re-pick reviewer).
    """
    start = (inputs.get("commit_start") or "").strip()
    end = (inputs.get("commit_end") or "").strip()
    pid = inputs.get("project_id", "")
    if not pid:
        root, err = _clone_or_err(inputs)
        if err:
            return err
        bare = None
    else:
        bare = _central_repo(pid)
        if not bare.exists():
            return {"ok": False, "error": "dépôt central inexistant"}
        root = None
    if not end:
        if root is not None:
            r = _git_run(root, ["rev-parse", "-q", "HEAD"])
        else:
            r = _git_run(bare, ["rev-parse", "-q", "HEAD"])
        end = (r.get("stdout") or "").strip() or "HEAD"
    # Si pas de start : diff du dernier commit (parent → livré).
    if not start:
        if root is not None:
            r = _git_run(root, ["rev-parse", "-q", f"{end}~1"])
        else:
            r = _git_run(bare, ["rev-parse", "-q", f"{end}~1"])
        start = (r.get("stdout") or "").strip()
        if not start:
            return {"ok": True, "diff": "", "files": [], "stat": "",
                    "note": "aucun parent (commit racine) — pas de diff"}
    if root is not None:
        diff = _git_run(root, ["diff", "--stat", start, end])
        full = _git_run(root, ["diff", start, end])
        names = _git_run(root, ["diff", "--name-status", start, end])
    else:
        diff = _git_run(bare, ["diff", "--stat", start, end])
        full = _git_run(bare, ["diff", start, end])
        names = _git_run(bare, ["diff", "--name-status", start, end])
    files = [ln.split("\t")[-1].strip()
             for ln in (names.get("stdout") or "").splitlines() if "\t" in ln]
    return {
        "ok": diff.get("exit_code", 1) == 0,
        "diff": full.get("stdout", ""),
        "stat": diff.get("stdout", ""),
        "files": files,
        "error": full.get("stderr", "") or None,
        "commit_start": start,
        "commit_end": end,
    }


def git_snapshot_start(inputs: dict, home: str) -> dict:
    """git_snapshot_start — POSE commit_start = HEAD du clone (MÉCANIQUE).

    Appelé par le greedy-coder après le clone/checkout, AVANT le travail. Le
    reviewer pourra ensuite diff commit_start → commit livré. Idempotent :
    si commit_start est déjà posé sur la tâche, ne change rien (les splits
    partagent le même point de départ).
    """
    root, err = _clone_or_err(inputs)
    if err:
        return err
    head = _git_run(root, ["rev-parse", "-q", "HEAD"])
    commit = (head.get("stdout") or "").strip()
    branch = _git_run(root, ["branch", "--show-current"])
    br = (branch.get("stdout") or "").strip()
    task_id = inputs.get("task_id")
    workspace_id = inputs.get("workspace_id", "")
    if commit and task_id is not None and workspace_id:
        try:
            from modules.sql.workspace import WorkspaceDB
            wdb = WorkspaceDB()
            row = wdb.conn.execute(
                "SELECT commit_start, branch_start FROM tasks "
                "WHERE task_id = ? AND workspace_id = ?",
                (int(task_id), workspace_id)).fetchone()
            if row is not None:
                if not (row["commit_start"] or "").strip():
                    wdb.conn.execute(
                        "UPDATE tasks SET commit_start = ?, branch_start = ? "
                        "WHERE task_id = ? AND workspace_id = ?",
                        (commit, br, int(task_id), workspace_id))
                    wdb.conn.commit()
            wdb.close()
        except Exception:
            pass
    return {"ok": True, "commit_start": commit, "branch": br}


def git_read_files(inputs: dict, home: str) -> dict:
    """git_read_files — lit le contenu de fichiers à un commit (MÉCANIQUE).

    `git show <commit>:<path>` pour chaque fichier demandé. Utilisé par le
    reviewer (ask_more_intel) pour examiner les fichiers du livrable quand le
    diff seul ne suffit pas. Lit depuis le DÉPÔT CENTRAL BARE (lecture pure,
    pas de lock). Retourne {content} avec en-têtes --- path ---."""
    pid = inputs.get("project_id", "")
    if not pid:
        root, err = _clone_or_err(inputs)
        if err:
            return err
        target = root
    else:
        bare = _central_repo(pid)
        if not bare.exists():
            return {"ok": False, "error": "dépôt central inexistant"}
        target = bare
    commit = (inputs.get("commit") or "").strip() or "HEAD"
    paths = inputs.get("paths") or []
    if isinstance(paths, str):
        import json as _json
        try:
            paths = _json.loads(paths)
        except Exception:
            paths = [p for p in paths.split(",") if p.strip()]
    if not paths:
        return {"ok": True, "content": "", "note": "aucun fichier demandé"}
    parts = []
    for p in paths:
        p = str(p).strip()
        if not p:
            continue
        r = _git_run(target, ["show", f"{commit}:{p}"])
        if r.get("exit_code", 1) == 0:
            parts.append(f"--- {p} ---\n{r.get('stdout', '')}")
        else:
            parts.append(f"--- {p} ---\n<absent ou non trouvé>")
    return {"ok": True, "content": "\n\n".join(parts),
            "paths": [str(p).strip() for p in paths if str(p).strip()]}


def git_log(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    oneline = inputs.get("oneline", True)
    r = _git_run(root, ["log"] + (["--oneline"] if oneline else []))
    r["lines"] = [l for l in r["stdout"].splitlines()]
    return r


def git_status(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    r = _git_run(root, ["status", "--porcelain"])
    r["clean"] = (r["stdout"].strip() == "")
    r["conflicts"] = _unmerged_files(root)
    return r


def git_merge(inputs: dict, home: str) -> dict:
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


def git_add(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    path = inputs.get("path", "")
    if path:
        return _git_run(root, ["add", "--", path])
    return _git_run(root, ["add", "-A"])


def git_resolve_conflict(inputs: dict, home: str) -> dict:
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


def git_fetch(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    return _git_run(root, ["fetch", "-q", "origin"])


def git_pull(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    branch = inputs.get("branch", "") or inputs.get("name", "")
    if branch:
        return _git_run(root, ["pull", "--no-edit", "origin", branch])
    return _git_run(root, ["pull", "--no-edit"])


def git_push(inputs: dict, home: str) -> dict:
    root, err = _clone_or_err(inputs)
    if err:
        return err
    branch = inputs.get("branch", "")
    ref = branch if branch else "HEAD"
    # Pull --rebase AVANT push : dans un swarm, d'autres membres peuvent avoir
    # avancé le repo central depuis notre clone (push non-fast-forward → erreur
    # → le LLM boucle). On réconcilie d'abord, best-effort.
    try:
        _git_run(root, ["pull", "-q", "--rebase", "origin"])
    except Exception:
        pass
    return _git_run(root, ["push", "-u", "origin", ref])


def git_push_remote(inputs: dict, home: str) -> dict:
    """Push du REPO CENTRAL LOCAL vers le remote distant (origin) sur une
    branche dédiée. Utilisé par l'intégrateur en fin de swarm : une fois les
    workers poussés sur le local, on pousse le local vers github/auto_code.

    inputs :
      - project_id : le repo central local (~/.modelweaver/repos/<pid>.git)
      - branch     : branche cible sur le distant (ex. auto_code_<team_id>)
    Le repo central local doit avoir un remote `origin` (distant réel).
    """
    pid = inputs.get("project_id", "")
    branch = inputs.get("branch", "")
    if not pid:
        return {"ok": False, "error": "project_id requis"}
    bare = _central_repo(pid)
    if not bare.exists():
        return {"ok": False, "error": f"repo central inexistant: {bare}"}
    if not branch:
        return {"ok": False, "error": "branch requis (ex. auto_code_<team_id>)"}
    # Git --git-dir pour opérer sur le repo BARE local.
    def _bare(*args, timeout: int = 60) -> dict:
        try:
            stdout, stderr, rc = Sandbox().run(
                ["git", "--git-dir", str(bare)] + list(args),
                shell=False, timeout=timeout)
            return {"stdout": stdout, "stderr": stderr, "exit_code": rc,
                    "ok": rc == 0}
        except SandboxError as e:
            return {"stdout": "", "stderr": str(e), "exit_code": -1, "ok": False}

    remotes = _bare("remote", "-v")
    if "origin" not in remotes.get("stdout", ""):
        return {"ok": False,
                "error": "repo central sans remote origin (push_remote impossible)"}
    # Pull du distant (best-effort) puis push de la branche locale.
    try:
        _bare("fetch", "-q", "origin")
    except Exception:
        pass
    # "Rien à pousser" : si la branche locale == distante, pas de push inutile.
    # (évite à l'intégrateur de repousser en boucle un travail déjà envoyé)
    try:
        r = _bare("rev-parse", "-q", "--verify", f"refs/heads/{branch}")
        if r["exit_code"] == 0:
            local = r["stdout"].strip()
            rd = _bare("rev-parse", "-q", "--verify",
                       f"refs/remotes/origin/{branch}")
            if rd["exit_code"] == 0 and rd["stdout"].strip() == local:
                return {"ok": True, "up_to_date": True,
                        "stdout": f"branche {branch} déjà à jour",
                        "stderr": "", "exit_code": 0}
    except Exception:
        pass
    return _bare("push", "origin", f"refs/heads/{branch}:refs/heads/{branch}")


def git_verify(inputs: dict, home: str) -> dict:
    """VÉRIFIE le travail (AUTO, pas de LLM) : stage + diff vs base + garde
    non-destructif. Retourne ok=True si le travail est réel et non-destructif,
    ok=False sinon (le FSM décide : check_with_llm ou retour en boucle).

    NE commit PAS, NE push PAS — c'est la vérif avant finalisation.
    """
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    if not pid or not aid:
        return {"ok": False, "error": "project_id et agent_id requis"}
    root, err = _clone_or_err({"project_id": pid, "agent_id": aid})
    if err:
        return err
    # Point de départ du diff : base_commit, sinon HEAD.
    base = (inputs.get("base_commit") or "").strip()
    if not base:
        r = _git_run(root, ["rev-parse", "-q", "HEAD"])
        base = r.get("stdout", "").strip() or ""
    # Stage + diff vs base
    _git_identity(root, aid)
    _git_add_safe(root)
    diff = _git_run(root, ["diff", "--cached", "--stat", base] if base
                    else ["diff", "--cached", "--stat"])
    if diff.get("exit_code") != 0:
        return {"ok": False, "error": f"diff vs base impossible: {diff.get('stderr','')[:200]}"}
    stat = diff.get("stdout", "")
    if not stat.strip():
        return {"ok": False,
                "error": "aucun changement vs base — travail non livré "
                         "(écris réellement le code)"}
    # Garde NON-DESTRUCTIF : ratio fichiers supprimés / modifiés.
    del_stat = _git_run(root, ["diff", "--cached", "--name-status", base] if base
                        else ["diff", "--cached", "--name-status"])
    del_names = []
    mod_names = []
    for line in (del_stat.get("stdout", "") or "").splitlines():
        parts = line.split("\t")
        status = (parts[0] or "").strip() if parts else ""
        fname = parts[-1].strip() if parts else ""
        if status.startswith("D"):
            del_names.append(fname)
        elif fname and status not in ("A",):
            mod_names.append(fname)
    total = len(del_names) + len(mod_names)
    if total > 0 and len(del_names) / total >= 0.30:
        return {"ok": False,
                "error": (f"diff DESTRUCTIF refusé : {len(del_names)}/{total} "
                          f"fichiers supprimés ({', '.join(del_names[:8])}…). "
                          f"Restaure les fichiers avant de continuer.")}
    return {"ok": True, "files_modified": mod_names, "files_deleted": del_names,
            "stdout": stat.strip(), "base": base}


def end_exec(inputs: dict, home: str) -> dict:
    """TERMINE l'exécution d'une tâche de code : vérifie que le travail est
    réel et non-destructif, committe, pousse sur le repo central local, et
    marque la tâche done. Appelé par le LLM quand il estime avoir fini (tool
    end_exec) — TOUTE la mécanique est automatique, le LLM n'a rien à coder.

    Gardes :
      1. Diff NON VIDE : refus si aucun changement vs le commit de base
         (base_commit de la tâche, sinon HEAD d'entrée, sinon master).
      2. Diff NON DESTRUCTIF : refus si trop de fichiers supprimés/renommés
         (seuil ~30% des fichiers modifiés) — un LLM qui "supprime la moitié
         des fichiers" ne doit PAS pouvoir livrer.

    inputs :
      - project_id : repo central local
      - agent_id   : agent propriétaire du clone
      - task_id / workspace_id : pour marquer la tâche done
      - base_commit : point de départ du diff (vide → HEAD d'entrée)
      - branch      : branche cible (vide → branche courante du clone)
    """
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    if not pid or not aid:
        return {"ok": False, "error": "project_id et agent_id requis"}
    root, err = _clone_or_err({"project_id": pid, "agent_id": aid})
    if err:
        return err

    # 0) Déterminer la branche cible (courante si non précisée)
    branch = inputs.get("branch", "")
    cur = _git_run(root, ["branch", "--show-current"])
    branch = branch or (cur.get("stdout", "").strip() or "master")

    # 1) Point de départ du diff : base_commit, sinon HEAD d'entrée (enregistré
    #    par le FSM dans une variable), sinon HEAD courant.
    base = (inputs.get("base_commit") or "").strip()
    if not base:
        head0 = (inputs.get("head_at_start") or "").strip()
        if head0:
            base = head0
    if not base:
        r = _git_run(root, ["rev-parse", "-q", "HEAD"])
        base = r.get("stdout", "").strip() or ""

    # 2) Stage + diff vs base
    _git_identity(root, aid)
    _git_add_safe(root)
    diff = _git_run(root, ["diff", "--cached", "--stat", base] if base
                    else ["diff", "--cached", "--stat"])
    if diff.get("exit_code") != 0:
        return {"ok": False, "error": f"diff vs base impossible: {diff.get('stderr','')[:200]}"}
    stat = diff.get("stdout", "")
    if not stat.strip():
        return {"ok": False,
                "error": "aucun changement vs base — travail non livré "
                         "(écris réellement le code avant end_exec)"}

    # 3) Garde NON-DESTRUCTIF : ratio fichiers supprimés / fichiers modifiés
    del_stat = _git_run(root, ["diff", "--cached", "--name-status", base] if base
                        else ["diff", "--cached", "--name-status"])
    del_names = []
    mod_names = []
    for line in (del_stat.get("stdout", "") or "").splitlines():
        parts = line.split("\t")
        status = (parts[0] or "").strip() if parts else ""
        fname = parts[-1].strip() if parts else ""
        if status.startswith("D"):
            del_names.append(fname)
        elif fname and status not in ("A",):
            mod_names.append(fname)
    total = len(del_names) + len(mod_names)
    if total > 0 and len(del_names) / total >= 0.30:
        return {"ok": False,
                "error": (f"diff DESTRUCTIF refusé : {len(del_names)}/{total} "
                          f"fichiers supprimés ({', '.join(del_names[:8])}…). "
                          f"Restaure les fichiers avant end_exec.")}

    # 4) Commit + push sur le repo central LOCAL (jamais de distant ici : les
    #    échanges avec le remote réel sont gérés par le team leader).
    commit_msg = inputs.get("commit_message", "") or "task done"
    cr = _git_run(root, ["commit", "-q", "-m", commit_msg])
    if cr.get("exit_code") != 0:
        # Rien à committer ? la garde diff vide l'aurait déjà refusé.
        return {"ok": False, "error": f"commit échoué: {cr.get('stderr','')[:200]}"}
    commit_hash = (cr.get("stdout") or "").strip()
    try:
        r = _git_run(root, ["rev-parse", "HEAD"])
        commit_hash = r.get("stdout", "").strip() or commit_hash
    except Exception:
        pass
    # Push sur le bare local (origin). Pull --rebase best-effort d'abord.
    try:
        _git_run(root, ["pull", "-q", "--rebase", "origin"])
    except Exception:
        pass
    pr = _git_run(root, ["push", "-u", "origin", branch])
    push_ok = pr.get("exit_code") == 0

    # 5) Transition du token : le travail est livré → le token passe à l'étape
    #    suivante du pipeline (ex. coding → code_review). C'est un
    #    token_task_modify (conceptuellement détruire le token courant et
    #    créer le suivant), PAS un simple task_done. La mécanique est
    #    automatique, le LLM n'a rien à coder. Se fait même si le push central
    #    a échoué (le commit local est la preuve de livraison).
    out = {"ok": True, "commit_hash": commit_hash, "branch": branch,
           "files_modified": mod_names, "files_deleted": del_names,
           "stdout": f"committed {commit_hash} on {branch}"}
    if not push_ok:
        out["partial"] = True
        out["warning"] = (f"commit fait mais push central échoué: "
                          f"{pr.get('stderr','')[:200]}")
    task_id = inputs.get("task_id")
    if task_id is not None:
        next_type = inputs.get("new_task_type", "code_review")
        try:
            from AgentsCatalogue.lib.workspacedb.task import modify_token
            m = modify_token({
                "workspace_id": inputs.get("workspace_id", ""),
                "task_id": task_id,
                "new_task_type": next_type,
                "status": "todo",
                "branch": branch,
                "commit_hash": commit_hash,
                "clear_assigned": True,
            }, home)
            if m.get("ok"):
                out["next_task_type"] = next_type
                out["transition"] = True
            else:
                out["warning"] = (out.get("warning", "") + " | "
                                  + m.get("error", "token_task_modify a échoué"))
        except Exception as e:
            out["warning"] = (out.get("warning", "") + f" | token_task_modify: {e}")
    return out


__skills__ = [
    "repo_init", "repo_list", "git_clone", "git_branch", "git_checkout",
    "git_commit", "git_diff", "git_log", "git_status", "git_merge", "git_add",
    "git_resolve_conflict", "git_fetch", "git_pull", "git_push",
    "git_push_remote", "end_exec", "git_verify",
    "git_review_diff", "git_snapshot_start", "git_read_files",
]
