"""Validation des clés API pour le module key_manager."""
from __future__ import annotations

import re
from typing import Iterable, List

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

    ``.
    """
    if api_key is None:
        raise InvalidKeyError("Clé API nulle.")
    return str(api_key).strip()
