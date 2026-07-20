"""Espace projet (opère sur le clone perso de l'agent).

Migré depuis services/skill_manager.py (_exec_project_*). Les helpers
_agent_clone et _safe_under sont reproduits à l'identique.
"""

import os
from pathlib import Path

from services._common import mw_home


def _agent_clone(agent_id: str, project_id: str) -> Path:
    return (mw_home() / "memagent" / str(agent_id)
            / "workspace" / str(project_id))


def _safe_under(root: Path, path: str) -> Path:
    root_res = root.resolve()
    norm = os.path.normpath(path)
    if norm.startswith("..") or norm.startswith("/"):
        norm = norm.lstrip("/")
    full = (root_res / norm).resolve()
    if full != root_res and not str(full).startswith(str(root_res) + os.sep):
        raise PermissionError(f"chemin hors racine: {path}")
    return full


def project_write(inputs: dict, ws: str) -> dict:
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    if not pid or not aid or not path:
        return {"ok": False, "error": "project_id, agent_id et path requis"}
    root = _agent_clone(aid, pid)
    if not root.exists():
        return {"ok": False, "error": "clone introuvable (git_clone ?)"}
    full = _safe_under(root, path)
    os.makedirs(full.parent, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(full)}


def project_read(inputs: dict, ws: str) -> dict:
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    path = inputs.get("path", "")
    if not pid or not aid or not path:
        return {"content": "", "error": "project_id, agent_id et path requis"}
    root = _agent_clone(aid, pid)
    full = _safe_under(root, path)
    if not full.exists():
        return {"content": "", "error": "introuvable"}
    return {"content": full.read_text(encoding="utf-8")}


def project_list(inputs: dict, ws: str) -> dict:
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    path = inputs.get("path", "")
    if not pid or not aid:
        return {"entries": [], "error": "project_id et agent_id requis"}
    root = _agent_clone(aid, pid)
    full = _safe_under(root, path) if path else root
    if not full.is_dir():
        return {"entries": []}
    return {"entries": [{"name": p.name, "type": "dir" if p.is_dir() else "file"}
                       for p in sorted(full.iterdir())]}


def project_tree(inputs: dict, ws: str) -> dict:
    pid = inputs.get("project_id", "")
    aid = inputs.get("agent_id", "")
    if not pid or not aid:
        return {"files": [], "error": "project_id et agent_id requis"}
    root = _agent_clone(aid, pid)
    if not root.exists():
        return {"files": []}
    files = [str(p.relative_to(root)) for p in root.rglob("*")
             if p.is_file() and ".git" not in p.relative_to(root).parts]
    return {"files": sorted(files)}


__skills__ = ["project_write", "project_read", "project_list", "project_tree"]
