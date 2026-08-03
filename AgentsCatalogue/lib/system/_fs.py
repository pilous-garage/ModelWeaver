"""Helpers de résolution de chemins partagés par les fonctions fichier de la librairie.

Déplacés depuis services/skill_manager.py (méthodes self._*). Adaptés en
fonctions module-level : (path, home) au lieu de (self, path). Utilisent
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


def safe_path(path: str, home_root: str) -> str:
    home_root_abs = os.path.abspath(home_root)
    norm = os.path.normpath(path)
    if os.path.isabs(norm):
        # Chemin absolu pointant déjà dans le home de l'agent : le re-router
        # vers son équivalent relatif — sinon on crée une arborescence
        # dupliquée home/{home_root}/… (ex. /home/…/agent_home/359/workspace/x
        # → agent_home/359/home/pierreloup2/… au lieu du workspace réel).
        if norm == home_root_abs or norm.startswith(home_root_abs + os.sep):
            norm = os.path.relpath(norm, home_root_abs)
        else:
            # Chemin absolu hors home (ex. /tmp/…) : réécrit sous le home
            # (comportement sandbox conservé).
            norm = norm.lstrip("/")
    full = os.path.join(home_root_abs, norm)
    full_norm = os.path.normpath(full)
    if not (full_norm == home_root_abs
            or full_norm.startswith(home_root_abs + os.sep)):
        raise PermissionError("chemin hors home")
    return full_norm


def read_index(home: str) -> dict:
    p = os.path.join(home, INDEX_FILE)
    if os.path.exists(p):
        try:
            return json.loads(Path(p).read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def write_index(home: str, idx: dict) -> None:
    os.makedirs(home, exist_ok=True)
    Path(os.path.join(home, INDEX_FILE)).write_text(
        json.dumps(idx, ensure_ascii=False, indent=2), encoding="utf-8")


def index_add(home: str, abs_file: str) -> None:
    home = os.path.abspath(home)
    imp = os.path.join(home, "important")
    af = os.path.abspath(abs_file)
    if not (af == imp or af.startswith(imp + os.sep)):
        return
    stem = os.path.splitext(os.path.basename(af))[0].lower()
    if not stem:
        return
    idx = read_index(home)
    if stem in idx:  # ambiguïté : déjà indexé
        return
    idx[stem] = os.path.relpath(af, home)
    write_index(home, idx)


def index_remove(home: str, abs_file: str) -> None:
    home = os.path.abspath(home)
    imp = os.path.join(home, "important")
    af = os.path.abspath(abs_file)
    if not (af == imp or af.startswith(imp + os.sep)):
        return
    stem = os.path.splitext(os.path.basename(af))[0].lower()
    idx = read_index(home)
    rel = os.path.relpath(af, home)
    if idx.get(stem) == rel:
        del idx[stem]
        write_index(home, idx)


def resolve_read_path(path: str, home: str) -> str:
    """Résout un chemin de lecture : alias d'index puis relatif sous home."""
    home = os.path.abspath(home)
    idx = read_index(home)
    base = path.split("/")[-1]
    if "/" not in path and path in idx:
        return os.path.join(home, idx[path])
    if "/" not in path and base in idx:
        return os.path.join(home, idx[base])
    return safe_path(path, home)


def classify_write_path(path: str, home: str) -> str:
    """Résout un chemin d'écriture : sous-dossier explicite honoré,
    sinon nom connu -> important/, sinon -> work/."""
    home = os.path.abspath(home)
    if "/" in path:
        return safe_path(path, home)
    stem = os.path.splitext(path)[0].lower()
    sub = "important" if stem in KNOWN_IMPORTANT else "work"
    return os.path.join(home, sub, path)
