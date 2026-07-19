"""Journalisation des appels LLM sur disque (WAL append, atomique).

Au lieu d'ecrir directement dans SQLite (qui souffre de « database is locked »
sous concurrence), chaque appel LLM pousse une ligne JSON dans un fichier
append-only : `.modelweaver/usage/real_call.log`. Le rassembleur
(`usage_collector.py`) replie ce fichier dans SQLite de façon asynchrone.

Avantages :
  - 0 perte sur lock SQLite (les appels n'ouvrent plus de connexion SQLite).
  - Survit a un crash (le fichier persiste sur disque).
  - Append atomique par ligne sur POSIX.

Configuration (RAM / disque) exposée via des globals centralisés, destinés à
être branchés plus tard sur un gestionnaire de configuration global.
"""

import json
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from services._common import mw_home


# ── Configuration (RAM / disque) ────────────────────────────────────────
# Les valeurs sont lues via le gestionnaire de config global
# (modules.config.config_manager) et restent donc réglables à chaud
# (panneau paramètres GUI / daemon). Voir modules/config/config_manager.py.
USAGE_DIR = "usage"                 # sous-dossier de mw_home()
LOG_FILENAME = "real_call.log"      # fichier d'entree (append-only)
ARCHIVE_PREFIX = "real_call.log."   # archives rotatives real_call.log.1, .2, ...

# Valeurs par defaut (utilisees seulement si le config manager absent).
_DF_ARCHIVE_BYTES = 100 * 1024 * 1024
_DF_RAM_BUFFER_BYTES = 10 * 1024 * 1024
_DF_MAX_ARCHIVES = 10
_DF_FSYNC_PER_LINE = True


def _cfg():
    """Retourne le config manager si importable, sinon None (fallback defauts)."""
    try:
        from modules.config.config_manager import config
        return config
    except Exception:
        return None


def MAX_ARCHIVE_BYTES() -> int:
    c = _cfg()
    return c.get("usage.archive_max_bytes", _DF_ARCHIVE_BYTES) if c else _DF_ARCHIVE_BYTES


def MAX_RAM_BUFFER_BYTES() -> int:
    c = _cfg()
    return c.get("usage.ram_buffer_bytes", _DF_RAM_BUFFER_BYTES) if c else _DF_RAM_BUFFER_BYTES


def MAX_ARCHIVES() -> int:
    c = _cfg()
    return c.get("usage.max_archives", _DF_MAX_ARCHIVES) if c else _DF_MAX_ARCHIVES


def FSYNC_PER_LINE() -> bool:
    c = _cfg()
    return c.get("usage.fsync_per_line", _DF_FSYNC_PER_LINE) if c else _DF_FSYNC_PER_LINE


# ── Etat module (singleton bas niveau) ─────────────────────────────────
_log_path: Optional[Path] = None
_lock = threading.Lock()


def _ensure_path() -> Optional[Path]:
    """Resout (et cree) le chemin du journal. Retourne le Path ou None."""
    global _log_path
    if _log_path is not None:
        return _log_path
    try:
        d = mw_home() / USAGE_DIR
        d.mkdir(parents=True, exist_ok=True)
        _log_path = d / LOG_FILENAME
        return _log_path
    except Exception:
        _log_path = None
        return None


def append_call(record: Dict[str, Any]) -> bool:
    """Ajoute un appel LLM au journal disque (1 ligne JSON).

    `record` contient au minimum provider_ref / model_ref / status / ts.
    On OUVRE le fichier a CHAQUE ecriture (O_APPEND par nom) : cela evite
    l'ecriture dans un inode deja renomme par le rassembleur (race
    append/renommage). L'append O_APPEND reste atomique par ligne.
    Retourne True si ecrit, False sinon (erreur disque).
    """
    line = json.dumps(record, separators=(",", ":"), ensure_ascii=False)
    data = (line + "\n").encode("utf-8")
    with _lock:
        path = _ensure_path()
        if path is None:
            return False
        fd = None
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
            os.write(fd, data)
            if FSYNC_PER_LINE():
                os.fsync(fd)
            return True
        except Exception:
            return False
        finally:
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass


def make_record(
    provider_ref: str,
    model_ref: str,
    status: str,
    agent_id: Optional[str] = None,
    endpoint_id: Optional[int] = None,
    key_ref: Optional[str] = None,
    tokens_in: int = 0,
    tokens_out: int = 0,
    cost: float = 0.0,
    error_code: Optional[str] = None,
    error_detail: Optional[str] = None,
    window_key: Optional[str] = None,
    sent_at: Optional[int] = None,
    received_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Construit un enregistrement normalisé prêt à être appendé."""
    now = int(time.time())
    return {
        "ts": now,
        "sent_at": sent_at or now,
        "received_at": received_at or now,
        "provider_ref": provider_ref,
        "endpoint_id": endpoint_id,
        "key_ref": key_ref,
        "model_ref": model_ref,
        "agent_id": agent_id,
        "tokens_in": int(tokens_in or 0),
        "tokens_out": int(tokens_out or 0),
        "cost": float(cost or 0.0),
        "status": status,
        "error_code": error_code,
        "error_detail": error_detail,
        "window_key": window_key
        or time.strftime("%Y-%m-%d-%H", time.gmtime(now)),
    }


def log_call(provider_ref: str, model_ref: str, status: str, **kwargs) -> bool:
    """Helper haut niveau : construit + append en une fois."""
    return append_call(make_record(provider_ref, model_ref, status, **kwargs))


def close() -> None:
    # Plus de fd persistant : rien a fermer (open-per-write).
    pass


def log_path() -> Optional[Path]:
    _ensure_path()
    return _log_path


def flush_if_large() -> None:
    """Garde de compatibilite : l'ecriture etant deja fsync par ligne,
    cette fonction ne fait plus rien d'utile mais reste callable."""
    pass


def reset_path_cache() -> None:
    """Oublie le chemin cache (apres suppression du fichier par un test)."""
    global _log_path
    _log_path = None
