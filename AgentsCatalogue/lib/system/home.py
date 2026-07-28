"""Gestion des montages et de la navigation dans le home agent.

Fonctions système pour :
- lister les montages disponibles (leader_workspace, projets, etc.)
- naviguer dans l'arborescence des montages de façon cloisonnée
- chaque montage est un sandbox : '..' ne peut pas en sortir

Les montages sont stockés dans {home}/mounts/index.json :
  {"leader_workspace": "/abs/team/workspace/path", ...}

Sécurité : la résolution de chemin est bornée à la racine du montage
(vers le haut) et à la racine du home (vers le bas).
"""

import json
import os
import re
from pathlib import Path

from ._fs import safe_path

MOUNTS_INDEX = "mounts"
MOUNTS_FILE = "index.json"


def _mounts_root(home: str) -> Path:
    return Path(home) / MOUNTS_INDEX


def _mounts_index_path(home: str) -> Path:
    return _mounts_root(home) / MOUNTS_FILE


def _normalize_mount_entry(entry):
    """Normalise une entrée mount en dict avec options."""
    if isinstance(entry, str):
        return {"target": entry, "read_only": False, "type": ""}
    if isinstance(entry, dict):
        return {
            "target": entry.get("target", ""),
            "read_only": bool(entry.get("read_only", False)),
            "type": entry.get("type", ""),
        }
    return {"target": "", "read_only": False, "type": ""}


def _load_mounts(home: str) -> dict:
    idx = _mounts_index_path(home)
    if not idx.exists():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_mounts(home: str, mounts: dict) -> None:
    root = _mounts_root(home)
    root.mkdir(parents=True, exist_ok=True)
    _mounts_index_path(home).write_text(
        json.dumps(mounts, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _resolve_mount(mount_name: str, home: str) -> Path:
    mounts = _load_mounts(home)
    entry = mounts.get(mount_name)
    if not entry:
        raise ValueError(f"montage '{mount_name}' introuvable")
    target = _normalize_mount_entry(entry)["target"]
    if not target:
        raise ValueError(f"montage '{mount_name}' sans target")
    return Path(target).resolve()


def _safe_mount_path(mount_name: str, rel_path: str, home: str) -> Path:
    mount_root = _resolve_mount(mount_name, home)
    if not rel_path:
        return mount_root
    norm = os.path.normpath(rel_path)
    if norm.startswith("/"):
        norm = norm.lstrip("/")
    full = mount_root / norm
    resolved = full.resolve()
    if not str(resolved).startswith(str(mount_root) + os.sep) and resolved != mount_root:
        raise PermissionError(
            f"chemin sort du montage '{mount_name}': {rel_path}"
        )
    return resolved


def list_mounts(inputs: dict, home: str) -> dict:
    """Renvoie la liste des montages enregistrés pour cet agent."""
    mounts = _load_mounts(home)
    result = {}
    for name, entry in mounts.items():
        opts = _normalize_mount_entry(entry)
        target_path = Path(opts["target"]).resolve()
        result[name] = {
            "target": str(target_path),
            "read_only": opts["read_only"],
            "type": opts["type"],
            "exists": target_path.exists(),
            "is_dir": target_path.is_dir() if target_path.exists() else False,
        }
    return {"mounts": result}


def mount_info(inputs: dict, home: str) -> dict:
    """Informations sur un montage spécifique."""
    mount_name = inputs.get("name", "")
    if not mount_name:
        return {"error": "name requis"}
    try:
        target = _resolve_mount(mount_name, home)
    except ValueError as e:
        return {"error": str(e)}
    mounts = _load_mounts(home)
    opts = _normalize_mount_entry(mounts.get(mount_name, {}))
    return {
        "name": mount_name,
        "target": str(target),
        "read_only": opts["read_only"],
        "type": opts["type"],
        "exists": target.exists(),
        "is_dir": target.is_dir() if target.exists() else False,
        "size": target.stat().st_size if target.exists() and target.is_file() else None,
    }


def register_mount(inputs: dict, home: str) -> dict:
    """Enregistre un nouveau point de montage (admin uniquement).

    options:
      read_only: bool (défaut False) — montage en lecture seule
      type: str — catégorie (workspace, project, config, …)
      description: str — label lisible
    """
    name = inputs.get("name", "")
    target = inputs.get("target", "")
    if not name or not target:
        return {"ok": False, "error": "name et target requis"}
    mounts = _load_mounts(home)
    if name in mounts:
        return {"ok": False, "error": f"montage '{name}' existe déjà"}
    target_path = Path(target).resolve()
    mounts[name] = {
        "target": str(target_path),
        "read_only": bool(inputs.get("read_only", False)),
        "type": inputs.get("type", ""),
        "description": inputs.get("description", ""),
    }
    _save_mounts(home, mounts)
    return {"ok": True, "name": name, "target": str(target_path)}


def unregister_mount(inputs: dict, home: str) -> dict:
    """Supprime un point de montage."""
    name = inputs.get("name", "")
    if not name:
        return {"ok": False, "error": "name requis"}
    mounts = _load_mounts(home)
    if name not in mounts:
        return {"ok": False, "error": f"montage '{name}' introuvable"}
    del mounts[name]
    _save_mounts(home, mounts)
    return {"ok": True, "name": name}


def ls(inputs: dict, home: str) -> dict:
    """Liste le contenu d'un dossier dans un montage (borné à la racine du montage).

    Si aucun montage n'est précisé, liste le contenu de home directement
    (work/, important/, ctx/, mem/, mounts/).
    """
    mount_name = inputs.get("mount", "")
    rel_path = inputs.get("path", "")

    if not mount_name:
        base = Path(home).resolve()
    else:
        try:
            base = _safe_mount_path(mount_name, "", home)
        except (ValueError, PermissionError) as e:
            return {"entries": [], "error": str(e)}

    if rel_path:
        try:
            base = _safe_mount_path(mount_name, rel_path, home) if mount_name else safe_path(rel_path, home)
        except (ValueError, PermissionError) as e:
            return {"entries": [], "error": str(e)}

    if not base.exists():
        return {"entries": [], "error": f"chemin inexistant: {rel_path or mount_name or 'home'}"}
    if not base.is_dir():
        return {"entries": [], "error": "n'est pas un dossier"}

    entries = []
    for name in sorted(base.iterdir()):
        try:
            stat = name.stat()
        except OSError:
            continue
        entries.append({
            "name": name.name,
            "type": "dir" if name.is_dir() else "file",
            "size": stat.st_size if name.is_file() else 0,
        })
    return {"entries": entries, "path": str(base.relative_to(Path(home).resolve()) if mount_name else str(base))}


def cd(inputs: dict, home: str) -> dict:
    """Valide un chemin relatif dans le contexte d'un montage (ou du home).

    Renvoie le chemin résolu si celui-ci reste dans les limites du montage
    (ou du home si aucun montage n'est spécifié).
    """
    mount_name = inputs.get("mount", "")
    path = inputs.get("path", "")
    if not path:
        return {"resolved": mount_name or "/", "error": ""}

    try:
        if mount_name:
            resolved = _safe_mount_path(mount_name, path, home)
        else:
            resolved = safe_path(path, home)
    except (ValueError, PermissionError) as e:
        return {"resolved": "", "error": str(e)}

    return {"resolved": str(resolved.relative_to(Path.home()))}


def tree(inputs: dict, home: str) -> dict:
    """Arborescence d'un dossier dans un montage (bornée à la racine du montage).

    max_depth=0 ou omit → illimité. max_depth=N → N niveaux maximum."""
    mount_name = inputs.get("mount", "")
    rel_path = inputs.get("path", "")
    try:
        max_depth = int(inputs.get("max_depth", 0))
    except (ValueError, TypeError):
        max_depth = 0

    if not mount_name:
        base = Path(home).resolve()
        label = "home"
    else:
        try:
            base = _safe_mount_path(mount_name, "", home)
            label = f"mount:{mount_name}"
        except (ValueError, PermissionError) as e:
            return {"tree": "", "error": str(e)}

    if rel_path:
        try:
            base = _safe_mount_path(mount_name, rel_path, home) if mount_name else safe_path(rel_path, home)
            label = rel_path
        except (ValueError, PermissionError) as e:
            return {"tree": "", "error": str(e)}

    if not base.exists():
        return {"tree": "", "error": f"chemin inexistant: {rel_path or mount_name or 'home'}"}
    if not base.is_dir():
        return {"tree": base.name, "error": ""}

    lines = []
    _build_tree(base, label, lines, Path(home).resolve(), max_depth, 0)
    return {"tree": "\n".join(lines)}


def _build_tree(current: Path, label: str, lines: list, home_resolved: Path, max_depth: int, depth: int) -> None:
    lines.append(label + "/")
    if max_depth > 0 and depth >= max_depth:
        return
    try:
        children = sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except OSError:
        return
    for child in children:
        try:
            rel = child.relative_to(home_resolved)
            display = str(rel)
        except ValueError:
            if child.is_dir():
                display = child.name + "/"
            else:
                display = child.name
        if child.is_dir():
            _build_tree(child, "  " + display, lines, home_resolved, max_depth, depth + 1)
        else:
            lines.append("  " + display)


def find(inputs: dict, home: str) -> dict:
    """Recherche récursive de fichiers dans un montage (ou home) par motif glob."""
    mount_name = inputs.get("mount", "")
    pattern = inputs.get("pattern", "*")

    if not mount_name:
        base = Path(home).resolve()
    else:
        try:
            base = _safe_mount_path(mount_name, "", home)
        except (ValueError, PermissionError) as e:
            return {"matches": [], "error": str(e)}

    matches = []
    for p in base.rglob(pattern):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        matches.append(str(rel))
    return {"matches": sorted(matches)}


def grep_content(inputs: dict, home: str) -> dict:
    """Recherche un motif texte dans le contenu des fichiers (grep -style)."""
    mount_name = inputs.get("mount", "")
    pattern = inputs.get("pattern", "")
    path = inputs.get("path", "")
    case_insensitive = inputs.get("case_insensitive", False)
    max_files = int(inputs.get("max_files", 50))

    if not pattern:
        return {"matches": [], "error": "pattern requis"}

    try:
        if mount_name:
            base = _safe_mount_path(mount_name, path, home) if path else _safe_mount_path(mount_name, "", home)
        elif path:
            base = safe_path(path, home)
        else:
            base = Path(home).resolve()
    except (ValueError, PermissionError) as e:
        return {"matches": [], "error": str(e)}

    if not base.exists():
        return {"matches": [], "error": f"chemin inexistant: {path or mount_name or 'home'}"}
    if base.is_file():
        files = [base]
    else:
        files = list(base.rglob("*"))

    flags = re.IGNORECASE if case_insensitive else 0
    compiled = re.compile(pattern, flags)
    results = []
    for f in sorted(files):
        if not f.is_file() or f.name == INDEX_FILE:
            continue
        if len(results) >= max_files:
            break
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        matching_lines = []
        for i, line in enumerate(content.splitlines(), 1):
            if compiled.search(line):
                matching_lines.append({"line": i, "content": line})
        if matching_lines:
            try:
                rel = f.relative_to(Path(home).resolve())
                display_path = str(rel)
            except ValueError:
                display_path = f.name
            results.append({
                "path": display_path,
                "matches": matching_lines,
                "match_count": len(matching_lines),
            })
    return {"matches": results, "pattern": pattern, "count": len(results)}


def wc(inputs: dict, home: str) -> dict:
    """Compte lignes, mots et caractères d'un fichier ou d'un répertoire."""
    mount_name = inputs.get("mount", "")
    path = inputs.get("path", "")

    try:
        if mount_name:
            target = _safe_mount_path(mount_name, path, home) if path else _safe_mount_path(mount_name, "", home)
        elif path:
            target = safe_path(path, home)
        else:
            target = Path(home).resolve()
    except (ValueError, PermissionError) as e:
        return {"error": str(e)}

    if target.is_file():
        content = target.read_text(encoding="utf-8", errors="replace")
        lines = content.splitlines()
        return {
            "path": str(target.relative_to(Path(home).resolve())),
            "lines": len(lines),
            "words": len(content.split()),
            "bytes": len(content.encode("utf-8")),
            "type": "file",
        }

    total_lines = 0
    total_words = 0
    total_bytes = 0
    file_count = 0
    for f in target.rglob("*"):
        if not f.is_file() or f.name == INDEX_FILE:
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")
            total_lines += len(content.splitlines())
            total_words += len(content.split())
            total_bytes += len(content.encode("utf-8"))
            file_count += 1
        except OSError:
            continue
    return {
        "path": str(target.relative_to(Path(home).resolve())),
        "lines": total_lines,
        "words": total_words,
        "bytes": total_bytes,
        "files": file_count,
        "type": "directory",
    }


def head(inputs: dict, home: str) -> dict:
    """Les N premières lignes d'un fichier (comme head -n)."""
    mount_name = inputs.get("mount", "")
    path = inputs.get("path", "")
    n = int(inputs.get("n", 10))

    if n < 1:
        return {"error": "n doit être >= 1", "lines": []}

    try:
        if mount_name:
            target = _safe_mount_path(mount_name, path, home) if path else _safe_mount_path(mount_name, "", home)
        elif path:
            target = safe_path(path, home)
        else:
            return {"error": "path requis", "lines": []}
    except (ValueError, PermissionError) as e:
        return {"error": str(e), "lines": []}

    if not target.exists() or not target.is_file():
        return {"error": f"fichier introuvable: {path}", "lines": []}

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        lines = []
        for i, line in enumerate(f):
            if i >= n:
                break
            lines.append({"line": i + 1, "content": line.rstrip("\n")})

    return {
        "path": str(target.relative_to(Path(home).resolve())),
        "lines": lines,
        "total_shown": len(lines),
        "n": n,
    }


def tail(inputs: dict, home: str) -> dict:
    """Les N dernières lignes d'un fichier (comme tail -n)."""
    mount_name = inputs.get("mount", "")
    path = inputs.get("path", "")
    n = int(inputs.get("n", 10))

    if n < 1:
        return {"error": "n doit être >= 1", "lines": []}

    try:
        if mount_name:
            target = _safe_mount_path(mount_name, path, home) if path else _safe_mount_path(mount_name, "", home)
        elif path:
            target = safe_path(path, home)
        else:
            return {"error": "path requis", "lines": []}
    except (ValueError, PermissionError) as e:
        return {"error": str(e), "lines": []}

    if not target.exists() or not target.is_file():
        return {"error": f"fichier introuvable: {path}", "lines": []}

    with open(target, "r", encoding="utf-8", errors="replace") as f:
        all_lines = f.readlines()

    shown = all_lines[-n:] if n < len(all_lines) else all_lines
    start = max(1, len(all_lines) - n + 1) if n < len(all_lines) else 1
    lines = [
        {"line": start + i, "content": line.rstrip("\n")}
        for i, line in enumerate(shown)
    ]

    return {
        "path": str(target.relative_to(Path(home).resolve())),
        "lines": lines,
        "total_shown": len(lines),
        "total_lines": len(all_lines),
        "n": n,
    }


BUNDLES_INDEX = "bundles"
BUNDLES_FILE = "index.json"


def _bundles_root(home: str) -> Path:
    return Path(home) / BUNDLES_INDEX


def _bundles_index_path(home: str) -> Path:
    return _bundles_root(home) / BUNDLES_FILE


def _load_bundles(home: str) -> dict:
    idx = _bundles_index_path(home)
    if not idx.exists():
        return {}
    try:
        return json.loads(idx.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_bundles(home: str, bundles: dict) -> None:
    root = _bundles_root(home)
    root.mkdir(parents=True, exist_ok=True)
    _bundles_index_path(home).write_text(
        json.dumps(bundles, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def list_bundles(inputs: dict, home: str) -> dict:
    """Liste les bundles disponibles."""
    bundles = _load_bundles(home)
    return {
        "bundles": {
            name: {"skills": skills, "count": len(skills)}
            for name, skills in bundles.items()
        }
    }


def get_bundle(inputs: dict, home: str) -> dict:
    """Retourne la liste des skills d'un bundle."""
    bundle_name = inputs.get("name", "")
    if not bundle_name:
        return {"error": "name requis"}
    bundles = _load_bundles(home)
    if bundle_name not in bundles:
        return {"error": f"bundle '{bundle_name}' introuvable"}
    return {"name": bundle_name, "skills": bundles[bundle_name]}


def register_bundle(inputs: dict, home: str) -> dict:
    """Enregistre un nouveau bundle de skills."""
    name = inputs.get("name", "")
    skills = inputs.get("skills", [])
    if not name or not isinstance(skills, list):
        return {"ok": False, "error": "name (string) et skills (list) requis"}
    bundles = _load_bundles(home)
    if name in bundles:
        return {"ok": False, "error": f"bundle '{name}' existe déjà"}
    bundles[name] = skills
    _save_bundles(home, bundles)
    return {"ok": True, "name": name, "skills": skills, "count": len(skills)}


__skills__ = [
    "list_mounts", "mount_info", "register_mount", "unregister_mount",
    "ls", "cd", "tree", "find",
    "grep_content", "wc", "head", "tail",
    "list_bundles", "get_bundle", "register_bundle",
]