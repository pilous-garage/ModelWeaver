"""Espace commun live (non versionné).

Migré depuis services/skill_manager.py (_exec_common_*). Les helpers
_common_root et _safe_under sont reproduits à l'identique.
"""

import os
from pathlib import Path

from services._common import mw_home


def _common_root(group_id: str) -> Path:
    return mw_home() / "common" / str(group_id)


def _safe_under(root: Path, path: str) -> Path:
    root_res = root.resolve()
    norm = os.path.normpath(path)
    if norm.startswith("..") or norm.startswith("/"):
        norm = norm.lstrip("/")
    full = (root_res / norm).resolve()
    if full != root_res and not str(full).startswith(str(root_res) + os.sep):
        raise PermissionError(f"chemin hors racine: {path}")
    return full


def common_write(inputs: dict, ws: str) -> dict:
    gid = inputs.get("group_id", "")
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    if not gid or not path:
        return {"ok": False, "error": "group_id et path requis"}
    root = _common_root(gid)
    full = _safe_under(root, path)
    os.makedirs(full.parent, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return {"ok": True, "path": str(full)}


def common_read(inputs: dict, ws: str) -> dict:
    gid = inputs.get("group_id", "")
    path = inputs.get("path", "")
    if not gid or not path:
        return {"content": "", "error": "group_id et path requis"}
    root = _common_root(gid)
    full = _safe_under(root, path)
    if not full.exists():
        return {"content": "", "error": "introuvable"}
    return {"content": full.read_text(encoding="utf-8")}


def common_list(inputs: dict, ws: str) -> dict:
    gid = inputs.get("group_id", "")
    path = inputs.get("path", "")
    if not gid:
        return {"entries": [], "error": "group_id requis"}
    root = _common_root(gid)
    full = _safe_under(root, path) if path else root
    if not full.is_dir():
        return {"entries": []}
    return {"entries": [{"name": p.name, "type": "dir" if p.is_dir() else "file"}
                       for p in sorted(full.iterdir())]}


def common_tree(inputs: dict, ws: str) -> dict:
    gid = inputs.get("group_id", "")
    if not gid:
        return {"files": [], "error": "group_id requis"}
    root = _common_root(gid)
    if not root.exists():
        return {"files": []}
    files = [str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()]
    return {"files": sorted(files)}


__skills__ = ["common_write", "common_read", "common_list", "common_tree"]
