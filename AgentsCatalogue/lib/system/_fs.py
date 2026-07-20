"""Helpers de résolution de chemins partagés par les fonctions fichier de la librairie.

Déplacés depuis services/skill_manager.py (méthodes self._*). Adaptés en
fonctions module-level : (path, ws) au lieu de (self, path). Utilisent
os.path (portable) — pas de logique OS-spécifique ici.
"""

import os
import json
from pathlib import Path

# Noms de fichiers « importants » routés automatiquement vers important/
KNOWN_IMPORTANT = {
    "todo", "readme", "version", "concept", "concepts", "notes", "note",
    "changelog", "ideas", "idea", "plan", "roadmap", "summary", "resume",
}
INDEX_FILE = "index.json"


def safe_path(path: str, workspace_root: str) -> str:
    norm = os.path.normpath(path)
    if norm.startswith("..") or norm.startswith("/"):
        norm = norm.lstrip("/")
    full = os.path.join(workspace_root, norm)
    if not full.startswith(os.path.abspath(workspace_root)):
        raise PermissionError("chemin hors workspace")
    return full


def read_index(ws: str) -> dict:
    p = os.path.join(ws, INDEX_FILE)
    if os.path.exists(p):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_index(ws: str, idx: dict) -> None:
    os.makedirs(ws, exist_ok=True)
    Path(os.path.join(ws, INDEX_FILE)).write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def index_add(ws: str, abs_file: str) -> None:
    home = os.path.abspath(ws)
    imp = os.path.join(home, "important")
    af = os.path.abspath(abs_file)
    if not (af == imp or af.startswith(imp + os.sep)):
        return
    stem = os.path.splitext(os.path.basename(af))[0].lower()
    if not stem:
        return
    idx = read_index(ws)
    if stem in idx:  # ambiguïté : déjà indexé
        return
    idx[stem] = os.path.relpath(af, home)
    write_index(ws, idx)


def index_remove(ws: str, abs_file: str) -> None:
    home = os.path.abspath(ws)
    imp = os.path.join(home, "important")
    af = os.path.abspath(abs_file)
    if not (af == imp or af.startswith(imp + os.sep)):
        return
    stem = os.path.splitext(os.path.basename(af))[0].lower()
    idx = read_index(ws)
    rel = os.path.relpath(af, home)
    if idx.get(stem) == rel:
        del idx[stem]
        write_index(ws, idx)


def resolve_read_path(path: str, ws: str) -> str:
    """Résout un chemin de lecture : alias d'index puis relatif sous home."""
    home = os.path.abspath(ws)
    idx = read_index(ws)
    base = path.split("/")[-1]
    if "/" not in path and path in idx:
        return os.path.join(home, idx[path])
    if "/" not in path and base in idx:
        return os.path.join(home, idx[base])
    return safe_path(path, ws)


def classify_write_path(path: str, ws: str) -> str:
    """Résout un chemin d'écriture : sous-dossier explicite honoré,
    sinon nom connu -> important/, sinon -> work/."""
    home = os.path.abspath(ws)
    if "/" in path:
        return safe_path(path, ws)
    stem = os.path.splitext(path)[0].lower()
    sub = "important" if stem in KNOWN_IMPORTANT else "work"
    return os.path.join(home, sub, path)
