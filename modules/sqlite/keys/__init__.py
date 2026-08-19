"""Domaine de clés API sécurisé (keyring OS + fallback Fernet).

La DB (keys.db) ne contient QUE les métadonnées + un `ref` UUID ; les
secrets vivent dans le keyring OS (ou fichier Fernet chiffré en headless).
AUCUNE clé en clair sur disque. Voir key_manager.py.
"""
from __future__ import annotations

from modules.sqlite.keys.key_manager import (
    KeyManager, KeyLockedError, WRITE_KEYS_TOKEN,
)

__all__ = ["KeyManager", "KeyLockedError", "WRITE_KEYS_TOKEN"]
