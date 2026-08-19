"""read — accès en lecture aux métadonnées de clés (domaine keys).

Le secret lui-même n'est JAMAIS lu ici : il vient du keyring OS (via
KeyManager). Ces fonctions exposent la couche métadonnées sqlite.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.keys.key_manager import KeyManager


def get_key_by_ref(ref: str) -> Optional[Dict[str, Any]]:
    return KeyManager().get_key_by_ref(ref)


def list_keys(identity: Optional[str] = None, tag: Optional[str] = None,
              provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
    return KeyManager().list_keys(identity=identity, tag=tag,
                                   provider_ref=provider_ref)


def list_providers() -> List[str]:
    return KeyManager().list_providers()


__all__ = ["get_key_by_ref", "list_keys", "list_providers"]
