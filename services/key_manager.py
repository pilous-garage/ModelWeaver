"""Service key_manager : gestion des clés API (wrapper autour du module).

Usage depuis les handlers daemon :
    from services.key_manager import set_key, get_key, list_keys, delete_key, set_lock, onboard
"""
from modules.key_manager.key_manager_module import KeyManager as _KeyManager, KeyLockedError


_km = None


def _get_km(db=None):
    global _km
    if _km is None:
        _km = _KeyManager(db=db)
    return _km


def set_key(params: dict) -> dict:
    km = _get_km(db=params.get("_db"))
    return km.set(params["name"], params.get("value"), params.get("origin"))


def get_key(params: dict) -> dict:
    km = _get_km(db=params.get("_db"))
    return km.get(params["name"])


def list_keys(_params: dict) -> dict:
    km = _get_km()
    return km.list()


def delete_key(params: dict) -> dict:
    km = _get_km(db=params.get("_db"))
    return km.delete(params["ref"])


def set_lock(params: dict) -> dict:
    km = _get_km(db=params.get("_db"))
    return km.set_lock(params["ref"], params.get("locked", True))


def onboard(params: dict) -> dict:
    from modules.key_manager.onboarder import Onboarder
    o = Onboarder()
    return o.onboard(env_path=params.get("env_path", ""))
