"""manifest_store — MANIFEST = FICHIER OUVERT (Idée 18, O1).

Le manifest (team YAML / agent service YAML) est un fichier OUVERT géré par
ModelWeaver. Règles :
  1. Les modifications se font UNIQUEMENT via ModelWeaver (routes daemon/FSM/
     agent_manager) — les modifs externes du fichier sont IGNORÉES à la lecture
     (le fichier n'est pas rechargé à chaque accès).
  2. On ne RÉÉCRIT pas le fichier si aucune modification n'a été faite via
     ModelWeaver.
  3. À l'enregistrement d'une modification, on vérifie que le fichier n'a pas
     été réécrit entre-temps (garde anti-écrasement : on compare le contenu
     connu au contenu disque AVANT d'écrire). Si une écriture concurrente a eu
     lieu, on refuse (ou on re-merge) plutôt que d'écraser.

État : chaque fichier ouvert garde (path, mtime, hash_au_chargement,
dirty: bool). dirty passe à True quand une mutation via ModelWeaver modifie le
spec ; la réécriture n'a lieu que si dirty ET que le disque n'a pas bougé.
Best-effort : ne lève jamais (les erreurs sont loggées et renvoyées).
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Any, Dict, Optional

# Registry : path → OpenManifest (le même pour toute la durée de vie du process).
_OPEN: Dict[str, "OpenManifest"] = {}
_LOCK = threading.Lock()


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class OpenManifest:
    """Un manifest ouvert : contenu connu + état de propreté.

    - ``content`` : les octets au moment du dernier chargement (source de
      vérité pour la garde anti-écrasement).
    - ``dirty`` : True si ModelWeaver a muté le spec depuis le chargement.
    """

    def __init__(self, path: Path, content: bytes):
        self.path = Path(path)
        self.content = content
        self.hash = _file_hash(content)
        self.dirty = False
        self.version = 0   # nb de réécritures réussies via ModelWeaver

    def mark_dirty(self):
        self.dirty = True

    def needs_write(self) -> bool:
        """Réécrire ? Uniquement si ModelWeaver a modifié (jamais sinon)."""
        return self.dirty


def open_manifest(path: Path | str) -> OpenManifest:
    """Ouvre (ou réutilise) le manifest du fichier.

    IMPORTANT : le contenu n'est re-lu que la PREMIÈRE fois pour ce process —
    les modifications externes du fichier sont IGNORÉES ensuite (règle 1)."""
    p = Path(path)
    with _LOCK:
        if str(p) in _OPEN:
            return _OPEN[str(p)]
        try:
            content = p.read_bytes()
        except Exception:
            content = b""
        om = OpenManifest(p, content)
        _OPEN[str(p)] = om
        return om


def close_manifest(path: Path | str) -> None:
    """Ferme le manifest (l'oubli du process — une réouverture re-lit le disque)."""
    with _LOCK:
        _OPEN.pop(str(path), None)


def save_manifest(path: Path | str, new_content: bytes,
                  force: bool = False) -> Dict[str, Any]:
    """Écrit le manifest APRÈS vérification anti-écrasement.

    Règles :
      - si le manifest n'est pas dirty (aucune modif via ModelWeaver) → on
        n'écrit PAS (règle 2) ;
      - sinon on compare le contenu DISQUE au contenu connu (hash) : si le
        fichier a bougé depuis le chargement (écriture concurrente), on
        REFUSE (règle 3) — sauf `force`.
    Retourne {ok, written, reason}.
    """
    om = open_manifest(path)
    if not om.needs_write() and not force:
        return {"ok": True, "written": False, "reason": "aucune modif via ModelWeaver"}
    try:
        current = om.path.read_bytes()
    except Exception:
        current = None
    if current is not None and _file_hash(current) != om.hash and not force:
        return {
            "ok": False, "written": False,
            "reason": "le fichier a été réécrit depuis le chargement — "
                      "écrasement refusé (règle 3)",
        }
    try:
        om.path.write_bytes(new_content)
        om.content = new_content
        om.hash = _file_hash(new_content)
        om.dirty = False
        om.version += 1
        return {"ok": True, "written": True, "reason": "écrit via ModelWeaver",
                "version": om.version}
    except Exception as e:
        return {"ok": False, "written": False, "reason": str(e)}


def write_yaml(path: Path | str, data: Any) -> Dict[str, Any]:
    """Sérialise `data` en YAML et l'écrit via save_manifest (avec la garde)."""
    import yaml
    try:
        content = yaml.safe_dump(data, allow_unicode=True, sort_keys=False,
                                 default_flow_style=False)
    except Exception as e:
        return {"ok": False, "written": False, "reason": f"dump YAML: {e}"}
    return save_manifest(path, content.encode())


def forget(path: Path | str) -> None:
    close_manifest(path)