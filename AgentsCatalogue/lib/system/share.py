"""Accès aux ressources partagées d'une équipe via la team_workspace.

Les fonctions partagent un même sous-dossier de la team_workspace
accessible par tous les membres de l'équipe.

Exemple : un dossier /docker dans le leader_workspace
accessible comme share(docker) par tous les workers.
"""

import json
import os
from pathlib import Path

from ._fs import safe_path, read_index, write_index, INDEX_FILE

TEAM_SHARES_DIR = "shares"
SHARES_INDEX = ".shares.json"


def _shares_root(home: str) -> Path:
    return Path(home) / TEAM_SHARES_DIR


def _shares_index_path(home: str) -> Path:
    return _shares_root(home) / SHARES_INDEX


def _load_shares(home: str) -> dict:
    idx = _shares_index_path(home)
    if not idx.exists():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_shares(home: str, shares: dict) -> None:
    root = _shares_root(home)
    root.mkdir(parents=True, exist_ok=True)
    _shares_index_path(home).write_text(
        json.dumps(shares, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _share_path(share_name: str, home: str) -> Path:
    shares = _load_shares(home)
    mount_name = shares.get(share_name)
    if not mount_name:
        raise ValueError(f"partage '{share_name}' inconnu")
    return Path(mount_name).resolve()


def _safe_share_path(share_name: str, rel_path: str, home: str) -> Path:
    share_root = _share_path(share_name, home)
    if not rel_path:
        return share_root
    norm = os.path.normpath(rel_path)
    if norm.startswith("/"):
        norm = norm.lstrip("/")
    full = share_root / norm
    resolved = full.resolve()
    if not str(resolved).startswith(str(share_root) + os.sep) and resolved != share_root:
        raise PermissionError(
            f"chemin sort du partage '{share_name}': {rel_path}"
        )
    return resolved


def share_list(inputs: dict, home: str) -> dict:
    """Liste les partages enregistrés dans ce home."""
    shares = _load_shares(home)
    result = {}
    for name, target in shares.items():
        target_path = Path(target).resolve()
        result[name] = {
            "target": str(target_path),
            "exists": target_path.exists(),
            "is_dir": target_path.is_dir() if target_path.exists() else False,
        }
    return {"shares": result}


def share_read(inputs: dict, home: str) -> dict:
    """Lit un fichier dans un partage."""
    share_name = inputs.get("share", "")
    rel_path = inputs.get("path", "")
    if not share_name:
        return {"error": "share requis"}
    if not rel_path:
        return {"error": "path requis"}
    try:
        full = _safe_share_path(share_name, rel_path, home)
    except (ValueError, PermissionError) as e:
        return {"error": str(e)}
    if not full.exists():
        return {"error": f"fichier introuvable: {rel_path}"}
    if full.is_dir():
        return {"error": f"c'est un dossier: {rel_path}"}
    return {"content": full.read_text(encoding="utf-8")}


def share_write(inputs: dict, home: str) -> dict:
    """Écrit un fichier dans un partage."""
    share_name = inputs.get("share", "")
    rel_path = inputs.get("path", "")
    content = inputs.get("content", "")
    if not share_name:
        return {"error": "share requis"}
    if not rel_path:
        return {"error": "path requis"}
    try:
        full = _safe_share_path(share_name, rel_path, home)
    except (ValueError, PermissionError) as e:
        return {"error": str(e)}
    full.parent.mkdir(parents=True, exist_ok=True)
    full.write_text(content, encoding="utf-8")
    return {"ok": True, "path": rel_path}


def share_register(inputs: dict, home: str) -> dict:
    """Enregistre un partage (lien vers un chemin externe)."""
    share_name = inputs.get("name", "")
    target = inputs.get("target", "")
    if not share_name or not target:
        return {"ok": False, "error": "name et target requis"}
    shares = _load_shares(home)
    if share_name in shares:
        return {"ok": False, "error": f"partage '{share_name}' existe déjà"}
    target_path = Path(target).resolve()
    shares[share_name] = str(target_path)
    _save_shares(home, shares)
    return {"ok": True, "name": share_name, "target": str(target_path)}


def share_unregister(inputs: dict, home: str) -> dict:
    """Supprime un partage."""
    share_name = inputs.get("name", "")
    if not share_name:
        return {"ok": False, "error": "name requis"}
    shares = _load_shares(home)
    if share_name not in shares:
        return {"ok": False, "error": f"partage '{share_name}' introuvable"}
    del shares[share_name]
    _save_shares(home, shares)
    return {"ok": True, "name": share_name}


def share_tree(inputs: dict, home: str) -> dict:
    """Arborescence d'un partage (bornée à la racine du partage)."""
    share_name = inputs.get("share", "")
    rel_path = inputs.get("path", "")
    if not share_name:
        return {"tree": "", "error": "share requis"}

    try:
        base = _safe_share_path(share_name, "", home)
        label = f"share:{share_name}"
    except (ValueError, PermissionError) as e:
        return {"tree": "", "error": str(e)}

    if rel_path:
        try:
            base = _safe_share_path(share_name, rel_path, home)
            label = rel_path
        except (ValueError, PermissionError) as e:
            return {"tree": "", "error": str(e)}

    if not base.exists():
        return {"tree": "", "error": f"chemin inexistant dans '{share_name}'"}
    if not base.is_dir():
        return {"tree": base.name, "error": ""}

    lines = []
    _build_share_tree(base, label, lines)
    return {"tree": "\n".join(lines)}


def _build_share_tree(current: Path, label: str, lines: list) -> None:
    lines.append(label + "/")
    try:
        children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except OSError:
        return
    for child in children:
        if child.is_dir():
            _build_share_tree(child, "  " + child.name + "/", lines)
        else:
            lines.append("  " + child.name)


__skills__ = [
    "share_list", "share_read", "share_write",
    "share_register", "share_unregister", "share_tree",
]