"""Fonctions fichier (relatif au home de l'agent).

Migrées depuis services/skill_manager.py (méthodes _exec_*). La logique
interne est conservée à l'identique ; les helpers de chemin sont ceux de
._fs (module-level).
"""

import os
import shutil
from pathlib import Path

from ._fs import (
    safe_path,
    classify_write_path,
    resolve_read_path,
    index_add,
    index_remove,
    INDEX_FILE,
)


def read_file(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, ws)
    with open(full, "r", encoding="utf-8") as f:
        return {"content": f.read()}


def write_file(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    full = classify_write_path(path, ws)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(content)
    index_add(ws, full)
    return {"result": f"Fichier {path} écrit"}


def append_file(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    full = classify_write_path(path, ws)
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "a", encoding="utf-8") as f:
        f.write(content)
    index_add(ws, full)
    return {"ok": True}


def delete_file(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, ws)
    if not os.path.exists(full):
        return {"ok": False, "error": "fichier introuvable"}
    if os.path.isdir(full):
        shutil.rmtree(full)
    else:
        os.remove(full)
    index_remove(ws, full)
    return {"ok": True}


def copy_file(inputs: dict, ws: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), ws)
    dst_input = inputs.get("dst", "")
    dst = classify_write_path(dst_input, ws) if "/" not in dst_input \
        else safe_path(dst_input, ws)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copy2(src, dst)
    index_add(ws, dst)
    return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(ws))}


def move_file(inputs: dict, ws: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), ws)
    dst_input = inputs.get("dst", "")
    dst = classify_write_path(dst_input, ws) if "/" not in dst_input \
        else safe_path(dst_input, ws)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(ws, src)
    shutil.move(src, dst)
    index_add(ws, dst)
    return {"ok": True, "dst": os.path.relpath(dst, os.path.abspath(ws))}


def mkdir(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    if "/" in path:
        full = safe_path(path, ws)
    else:
        full = os.path.join(os.path.abspath(ws), "work", path)
    os.makedirs(full, exist_ok=True)
    return {"ok": True, "path": os.path.relpath(full, os.path.abspath(ws))}


def list_dir(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, ws) if path else os.path.abspath(ws)
    if not os.path.isdir(full):
        return {"entries": [], "error": "n'est pas un dossier"}
    entries = []
    for name in sorted(os.listdir(full)):
        fp = os.path.join(full, name)
        entries.append({
            "name": name,
            "type": "dir" if os.path.isdir(fp) else "file",
            "size": os.path.getsize(fp) if os.path.isfile(fp) else 0,
        })
    return {"entries": entries}


def glob(inputs: dict, ws: str) -> dict:
    pattern = inputs.get("pattern", "*")
    base = os.path.abspath(ws)
    results = [str(p.relative_to(base)) for p in Path(base).glob(pattern)
               if not str(p).endswith(INDEX_FILE)]
    return {"matches": sorted(results)}


def file_info(inputs: dict, ws: str) -> dict:
    path = inputs.get("path", "")
    full = resolve_read_path(path, ws)
    if not os.path.exists(full):
        return {"exists": False}
    st = os.stat(full)
    return {
        "exists": True,
        "is_dir": os.path.isdir(full),
        "size": st.st_size,
        "mtime": st.st_mtime,
    }


def upgrade_important(inputs: dict, ws: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), ws)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    name = inputs.get("name") or os.path.basename(src)
    dst = os.path.join(os.path.abspath(ws), "important", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(ws, src)
    shutil.move(src, dst)
    index_add(ws, dst)
    return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(ws))}


def downgrade_important(inputs: dict, ws: str) -> dict:
    src = resolve_read_path(inputs.get("src", ""), ws)
    if not os.path.exists(src):
        return {"ok": False, "error": "source introuvable"}
    name = inputs.get("name") or os.path.basename(src)
    dst = os.path.join(os.path.abspath(ws), "work", name)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    index_remove(ws, src)
    shutil.move(src, dst)
    return {"ok": True, "path": os.path.relpath(dst, os.path.abspath(ws))}


__skills__ = [
    "read_file", "write_file", "append_file", "delete_file",
    "copy_file", "move_file", "mkdir", "list_dir", "glob",
    "file_info", "upgrade_important", "downgrade_important",
]
