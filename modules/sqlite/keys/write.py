"""write — accès en écriture aux métadonnées de clés (domaine keys).

set_key/delete_key gèrent AUSSI le keyring OS (secret) ; la partie sqlite
n'est que les métadonnées (ref, provider, tag, grade, health…).
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.keys.key_manager import KeyManager, WRITE_KEYS_TOKEN


def set_key(provider_ref: str, api_key: str, api_base: Optional[str] = None,
            identity: str = "default", tag: str = "paid",
            grade: Optional[str] = None,
            metadata: Optional[Dict[str, Any]] = None) -> Optional[str]:
    return KeyManager().set_key(provider_ref, api_key, api_base=api_base,
                                 identity=identity, tag=tag, grade=grade,
                                 metadata=metadata)


def delete_key(ref: str) -> bool:
    return KeyManager().delete_key(ref)


def update_health(ref: str, status: str) -> None:
    KeyManager().update_health(ref, status)


__all__ = ["set_key", "delete_key", "update_health", "WRITE_KEYS_TOKEN"]
