"""swarm_repo — repo global du swarm-as-llm (paramétrable, commit vierge).

Le swarm-as-llm répond à une requête en PRODUISANT des fichiers (code/docs).
On centralise cette production dans UN repo global `llm_as_swarm_repo` :
  - un commit VIERGE d'origine (vide ou avec des fichiers de base optionnels) ;
  - chaque requête = une BRANCHE dédiée (nom = requête/requete_id) créée depuis
    le commit vierge ;
  - `get_first_commit` retourne le commit vierge → le diff (vierge..HEAD) est le
    livrable réel de la requête (prompt + fichiers produits).

Le repo est PARAMÉTRABLE : `reset(minimal_repos=[...])` réinitialise le commit
vierge en y incluant les fichiers de base souhaités (ex. docs de règles
comportementales non-agent, conventions syntaxiques…). Par défaut : rien.

Emplacement : ~/.modelweaver/repos/llm_as_swarm_repo.git (repo BARE, même
mécanique que repo_init/git_clone des agents).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Dict, List, Optional

MW_HOME = Path(os.environ.get("MODELWEAVER_HOME") or Path.home() / ".modelweaver")
GLOBAL_REPO_NAME = "llm_as_swarm_repo"
GLOBAL_REPO = MW_HOME / "repos" / f"{GLOBAL_REPO_NAME}.git"


def _run(args: List[str], cwd: Optional[Path] = None) -> Dict[str, str]:
    """Exécute git, retourne {stdout, stderr, returncode}."""
    try:
        r = subprocess.run(["git"] + args, capture_output=True, text=True,
                           timeout=60, cwd=str(cwd) if cwd else None)
        return {"stdout": r.stdout.strip(), "stderr": r.stderr.strip(),
                "returncode": r.returncode}
    except Exception as e:  # noqa: BLE001
        return {"stdout": "", "stderr": str(e), "returncode": -1}


def _in_repo() -> bool:
    """Vrai si le repo global existe déjà (est un repo BARE git)."""
    return (GLOBAL_REPO / "HEAD").exists() and (GLOBAL_REPO / "objects").is_dir()


def ensure(minimal_repos: Optional[List[dict]] = None) -> Dict:
    """Crée le repo global + commit vierge s'il n'existe pas encore.

    `minimal_repos` : liste de {path, content} — fichiers de base à inclure
    dans le commit vierge (par défaut : aucun). Si le repo existe déjà, on ne
    touche à rien (voir reset pour réinitialiser).
    """
    if _in_repo():
        return {"ok": True, "repo": str(GLOBAL_REPO),
                "first_commit": first_commit(),
                "note": "repo déjà initialisé"}
    GLOBAL_REPO.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["init", "--bare", "-b", "master", str(GLOBAL_REPO)])
    if r["returncode"] != 0:
        return {"ok": False, "error": f"init: {r['stderr']}"}
    return reset(minimal_repos)


def reset(minimal_repos: Optional[List[dict]] = None) -> Dict:
    """RÉINITIALISE le repo global : nouveau commit vierge (branche master).

    - supprime toute l'historique (re-création du repo BARE) ;
    - y écrit `minimal_repos` (list de {path, content}) si fourni, sinon rien ;
    - retourne le hash du commit vierge (point de départ des branches).

    Usage : reset_du_repo(minimal_repos=[...]) quand on veut inclure des docs
    de base (règles non-agent, conventions…) dans le fond du swarm-as-llm.
    """
    # re-création propre du repo BARE (vider l'historique)
    import shutil
    if GLOBAL_REPO.exists():
        shutil.rmtree(GLOBAL_REPO, ignore_errors=True)
    GLOBAL_REPO.parent.mkdir(parents=True, exist_ok=True)
    r = _run(["init", "--bare", "-b", "master", str(GLOBAL_REPO)])
    if r["returncode"] != 0:
        return {"ok": False, "error": f"init: {r['stderr']}"}

    # travail dans un répertoire temporaire pour créer le commit vierge
    tmp = MW_HOME / "repos" / f".{GLOBAL_REPO_NAME}_work"
    import shutil as _sh
    if tmp.exists():
        _sh.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(parents=True, exist_ok=True)
    _run(["init", "-b", "master", str(tmp)], cwd=tmp)
    _run(["config", "user.email", "swarm@modelweaver.local"], cwd=tmp)
    _run(["config", "user.name", "swarm"], cwd=tmp)

    # fichiers de base optionnels
    for f in (minimal_repos or []):
        path = f.get("path", "") if isinstance(f, dict) else ""
        content = f.get("content", "") if isinstance(f, dict) else ""
        if not path:
            continue
        p = tmp / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")

    _run(["add", "-A"], cwd=tmp)
    # commit vierge : --allow-empty car un repo sans AUCUN fichier ne committe
    # pas par défaut (git refuse "nothing to commit").
    c = _run(["commit", "--allow-empty", "-m",
              "seed: commit vierge (base swarm-as-llm)"], cwd=tmp)
    if c["returncode"] != 0:
        _sh.rmtree(tmp, ignore_errors=True)
        return {"ok": False, "error": f"commit seed: {c['stderr']}"}
    first = c["stdout"].splitlines()[0] if c["stdout"] else ""
    # extraire le hash : format "[master (root-commit) <hash>] msg"
    import re as _re
    _m = _re.search(r"\(root-commit\)\s+([0-9a-f]{7,})", first)
    if _m:
        first = _m.group(1)
    # pousse master (avec le commit vierge) vers le repo BARE
    _run(["push", "-q", str(GLOBAL_REPO), "master:master"], cwd=tmp)
    _sh.rmtree(tmp, ignore_errors=True)

    return {"ok": True, "repo": str(GLOBAL_REPO), "first_commit": first,
            "minimal_files": [f.get("path") for f in (minimal_repos or [])]}


def first_commit() -> Optional[str]:
    """Hash du commit VIERGE (le plus ancien de master) — point de départ du
    diff d'une requête. None si le repo n'existe pas."""
    if not _in_repo():
        return None
    r = _run(["rev-list", "--max-parents=0", "HEAD"],
             cwd=str(GLOBAL_REPO))
    if r["returncode"] != 0:
        return None
    lines = [l for l in r["stdout"].splitlines() if l.strip()]
    return lines[0] if lines else None


def create_branch(requete_id: str) -> Dict:
    """Crée une branche `requete/<requete_id>` depuis le commit vierge.

    Les agents greedy clonent le repo global et checkout cette branche pour
    y produire leurs livrables. Retourne {ok, branch, first_commit}."""
    if not _in_repo():
        return {"ok": False, "error": "repo global inexistant (ensure ?)"}
    first = first_commit()
    branch = f"requete/{requete_id}"
    # supprime une éventuelle branche existante du même nom (re-run)
    _run(["branch", "-D", branch], cwd=str(GLOBAL_REPO))
    r = _run(["branch", branch, first], cwd=str(GLOBAL_REPO))
    if r["returncode"] != 0:
        return {"ok": False, "error": f"branch: {r['stderr']}"}
    return {"ok": True, "branch": branch, "first_commit": first,
            "repo": GLOBAL_REPO_NAME}


def diff_for(requete_id: str) -> Dict:
    """Diff du travail produit pour une requête : `git diff first_commit..HEAD`
    sur la branche `requete/<requete_id>`. Retourne {ok, diff, files}."""
    branch = f"requete/{requete_id}"
    if not _in_repo():
        return {"ok": False, "error": "repo global inexistant"}
    first = first_commit()
    if not first:
        return {"ok": False, "error": "commit vierge introuvable"}
    # travail dans un clone temporaire pour lire le diff
    tmp = MW_HOME / "repos" / f".{GLOBAL_REPO_NAME}_diff"
    import shutil as _sh
    if tmp.exists():
        _sh.rmtree(tmp, ignore_errors=True)
    c = _run(["clone", "-q", str(GLOBAL_REPO), str(tmp)])
    if c["returncode"] != 0:
        return {"ok": False, "error": f"clone: {c['stderr']}"}
    _run(["checkout", "-q", branch], cwd=tmp)
    d = _run(["diff", first, "HEAD"], cwd=tmp)
    files = _run(["diff", "--name-only", first, "HEAD"], cwd=tmp)
    _sh.rmtree(tmp, ignore_errors=True)
    return {"ok": True, "diff": d["stdout"],
            "files": [f for f in files["stdout"].splitlines() if f.strip()]}


def write_seed(branch: str, prompt: str,
               files: Optional[Dict[str, str]] = None) -> Dict:
    """Dépose la prompt originelle + les fichiers fournis sur la branche d'une
    requête (clone temporaire → commit seed `PROMPT.md` + fichiers → push).

    Le commit seed de la requête EST le point de départ du diff : le travail
    des greedy (commits suivants sur la branche) sera différé contre lui.
    """
    if not _in_repo():
        return {"ok": False, "error": "repo global inexistant (ensure ?)"}
    tmp = MW_HOME / "repos" / f".{GLOBAL_REPO_NAME}_seed"
    import shutil as _sh
    if tmp.exists():
        _sh.rmtree(tmp, ignore_errors=True)
    c = _run(["clone", "-q", str(GLOBAL_REPO), str(tmp)])
    if c["returncode"] != 0:
        return {"ok": False, "error": f"clone: {c['stderr']}"}
    _run(["checkout", "-q", branch], cwd=tmp)
    _run(["config", "user.email", "swarm@modelweaver.local"], cwd=tmp)
    _run(["config", "user.name", "swarm"], cwd=tmp)
    (tmp / "PROMPT.md").write_text(str(prompt or ""), encoding="utf-8")
    for name, content in (files or {}).items():
        p = tmp / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(str(content), encoding="utf-8")
    _run(["add", "-A"], cwd=tmp)
    _run(["commit", "-m", f"seed requête ({branch})"], cwd=tmp)
    r = _run(["push", "-q", "origin", branch], cwd=tmp)
    _sh.rmtree(tmp, ignore_errors=True)
    if r["returncode"] != 0:
        return {"ok": False, "error": f"push seed: {r['stderr']}"}
    return {"ok": True, "branch": branch}
