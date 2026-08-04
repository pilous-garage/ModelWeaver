"""Validation des clés API pour le module key_manager."""
from __future__ import annotations

import re
from typing import Iterable

_VALID_PREFIXES = ("sk-", "gsk-")
_MIN_LENGTH = 32
_MAX_LENGTH = 128
_ALLOWED_RE = re.compile(r"^[A-Za-z0-9\-_]+$")


class InvalidKeyError(Exception):
    """Erreur de validation de clé API."""


def validate_key(api_key: str, *, prefixes: Iterable[str] | None = None,
                 min_length: int = _MIN_LENGTH,
                 max_length: int = _MAX_LENGTH) -> str:
    """Valide une clé API et retourne la valeur normalisée.

    Règles appliquées :
    - non nulle, non vide après strip ;
    - préfixe autorisé ;
    - longueur dans [min_length, max_length] ;
    - caractères autorisés uniquement.
    """
    if api_key is None:
        raise InvalidKeyError("Clé API nulle.")
    normalized = str(api_key).strip()
    if not normalized:
        raise InvalidKeyError("Clé API vide.")
    prefixes = tuple(prefixes) if prefixes is not None else _VALID_PREFIXES
    if prefixes and not any(normalized.startswith(p) for p in prefixes):
        allowed = ", ".join(prefixes)
        raise InvalidKeyError(
            f"Clé API invalide : préfixe '{normalized[:4]}...' non autorisé (autorisés : {allowed})."
        )
    if not (min_length <= len(normalized) <= max_length):
        raise InvalidKeyError(
            f"Clé API invalide : longueur {len(normalized)} hors bornes [{min_length}, {max_length}]."
        )
    if not _ALLOWED_RE.match(normalized):
        raise InvalidKeyError(
            "Clé API invalide : caractères non autorisés détectés."
        )
    return normalized
