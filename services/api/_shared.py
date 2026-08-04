"""État partagé entre le daemon HTTP et ses handlers.

Contient les constantes de version, les singletons lazy (DB, key manager, LLM)
et les utilitaires _wrap / _quiet.

N'importe PAS depuis services.api.* (pas de circular import).
"""
import contextlib
import sys
import threading
from pathlib import Path
from typing import Callable


API_VERSION = "v1"
MW_VERSION = "0.8.10"

# ── Racine du dépôt (référence pour les chemins absolus) ────────────────
def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent

# ── Singletons lazy (initialisés par _get_*) ────────────────────────────
_DB_LOCK = threading.Lock()
_MW_INSTANCE = None
_CAT_INSTANCE = None
_KM_INSTANCE = None
_LLM_INSTANCE = None
_RT_INSTANCE = None


def _mw_dir() -> Path:
    from services._common import mw_home
    d = mw_home()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _get_mw():
    from modules.sql.sql_module import ModelWeaverDB
    from services._common import _db_paths
    global _MW_INSTANCE
    if _MW_INSTANCE is None:
        with _DB_LOCK:
            if _MW_INSTANCE is None:
                mw_path, _ = _db_paths()
                _MW_INSTANCE = ModelWeaverDB(mw_path)
    return _MW_INSTANCE


def _get_cat():
    from modules.sql.sql_module import CatalogueDB
    from services._common import _db_paths
    global _CAT_INSTANCE
    if _CAT_INSTANCE is None:
        with _DB_LOCK:
            if _CAT_INSTANCE is None:
                _, cat_path = _db_paths()
                _CAT_INSTANCE = CatalogueDB(cat_path)
    return _CAT_INSTANCE


def _get_rt():
    from modules.sql.sql_module import RuntimeDB
    from services._common import runtime_db_path
    global _RT_INSTANCE
    if _RT_INSTANCE is None:
        with _DB_LOCK:
            if _RT_INSTANCE is None:
                _RT_INSTANCE = RuntimeDB(runtime_db_path())
    return _RT_INSTANCE


def _get_km():
    from modules.key_manager.key_manager_module import KeyManager
    global _KM_INSTANCE
    if _KM_INSTANCE is None:
        _KM_INSTANCE = KeyManager(db=_get_mw())
    return _KM_INSTANCE


def _get_llm():
    from modules.llm_manager.llm_manager_module import LLMManager
    global _LLM_INSTANCE
    if _LLM_INSTANCE is None:
        _LLM_INSTANCE = LLMManager(cat=_get_cat(), km=_get_km())
    return _LLM_INSTANCE


def _get_bridge():
    """Bridge actif via la façade LLMManager (DirectBridge par défaut)."""
    return _get_llm().get_bridge()


def _wrap(fn: Callable) -> Callable:
    """Adapte fn() sans paramètre en handler(params)->dict."""
    def handler(_params):
        with contextlib.redirect_stdout(sys.stderr):
            return fn()
    return handler


def _quiet(fn, *args):
    with contextlib.redirect_stdout(sys.stderr):
        return fn(*args)
