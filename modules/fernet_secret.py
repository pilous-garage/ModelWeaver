"""
fernet_secret.py
----------------
Utilitaire de gestion de secrets Fernet sans dépendre uniquement de
/etc/machine-id, qui n'est pas vraiment secret.

Approche :
- Lire un secret utilisateur depuis le keyring système (secretstorage / keyring).
- Combiner ce secret avec un salt aléatoire stocké dans le keyring.
- Si le keyring n'est pas disponible, utiliser un salt aléatoire persisté
  dans un fichier local protégé (chmod 600) comme dernier recours.
- En déduire une clé Fernet déterministe mais suffisamment distincte
  de la machine-id brute.

Ce module ne modifie pas le comportement par défaut existant ; il fournit
une alternative plus sûre que l'utilisation brute de /etc/machine-id.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Optional

try:
    from cryptography.fernet import Fernet, InvalidToken
except Exception:  # pragma: no cover
    Fernet = None  # type: ignore[misc,assignment]
    InvalidToken = Exception  # type: ignore[misc,assignment]

try:
    import keyring  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    keyring = None  # type: ignore[assignment]

try:
    import secretstorage  # type: ignore[import-untyped]
except Exception:  # pragma: no cover
    secretstorage = None  # type: ignore[assignment]


SERVICE_NAME = "mw-swarm-fernet"
SALT_ENTRY = "fernet-salt"
USER_SECRET_ENTRY = "fernet-user-secret"
DEFAULT_FALLBACK_FILE = Path.home() / ".local" / "share" / "mw-swarm" / "fernet_salt"


def _read_machine_id() -> str:
    """Lit /etc/machine-id sans lever d'exception fatale."""
    try:
        return Path("/etc/machine-id").read_text(encoding="utf-8").strip()
    except Exception:
        return ""


def _ensure_fallback_file(path: Path) -> bytes:
    """Crée un fichier de salt local protégé (chmod 600) si nécessaire."""
    path = path.expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        salt = os.urandom(32)
        path.write_bytes(salt)
        try:
            path.chmod(0o600)
        except Exception:
            pass
        return salt
    return path.read_bytes()


def _get_keyring_salt() -> Optional[bytes]:
    """Récupère un salt depuis le keyring système."""
    if keyring is None:
        return None
    try:
        raw = keyring.get_password(SERVICE_NAME, SALT_ENTRY)
        if raw is None:
            return None
        return bytes.fromhex(raw)
    except Exception:
        return None


def _set_keyring_salt(salt: bytes) -> bool:
    """Stocke un salt dans le keyring système."""
    if keyring is None:
        return False
    try:
        keyring.set_password(SERVICE_NAME, SALT_ENTRY, salt.hex())
        return True
    except Exception:
        return False


def _get_keyring_user_secret() -> Optional[bytes]:
    """Récupère un secret utilisateur depuis le keyring."""
    if keyring is None:
        return None
    try:
        raw = keyring.get_password(SERVICE_NAME, USER_SECRET_ENTRY)
        if raw is None:
            return None
        return raw.encode("utf-8")
    except Exception:
        return None


def _set_keyring_user_secret(secret: bytes) -> bool:
    """Stocke un secret utilisateur dans le keyring."""
    if keyring is None:
        return False
    try:
        keyring.set_password(SERVICE_NAME, USER_SECRET_ENTRY, secret.decode("utf-8", errors="replace"))
        return True
    except Exception:
        return False


def _derive_key(machine_id: str, user_secret: bytes, salt: bytes) -> bytes:
    """
    Dérive une clé Fernet depuis :
    - machine-id
    - secret utilisateur
    - salt aléatoire
    """
    if Fernet is None:
        raise RuntimeError("Le paquet 'cryptography' est requis pour utiliser Fernet.")

    base = hashlib.sha256()
    base.update(machine_id.encode("utf-8"))
    base.update(b"|")
    base.update(user_secret)
    base.update(b"|")
    base.update(salt)
    digest = base.digest()

    # Fernet attend une base64url 32-octets.
    fernet_key = hashlib.sha256(digest).digest()[:32]
    import base64
    return base64.urlsafe_b64encode(fernet_key)


def get_or_create_fernet_key(fallback_file: Optional[Path] = None) -> bytes:
    """
    Retourne une clé Fernet stable pour la machine/utilisateur courant.

    Ordre de préférence :
    1. Secret utilisateur + salt dans le keyring.
    2. Salt dans le keyring + machine-id comme base.
    3. Salt local chiffré + machine-id comme base.
    """
    machine_id = _read_machine_id()
    salt_path = fallback_file or DEFAULT_FALLBACK_FILE

    # 1) Tentative avec secret utilisateur + salt keyring.
    user_secret = _get_keyring_user_secret()
    keyring_salt = _get_keyring_salt()
    if user_secret is not None and keyring_salt is not None:
        return _derive_key(machine_id, user_secret, keyring_salt)

    # 2) Tentative avec salt keyring seul + machine-id.
    if keyring_salt is not None:
        return _derive_key(machine_id, b"", keyring_salt)

    # 3) Fallback fichier local.
    salt = _ensure_fallback_file(salt_path)
    return _derive_key(machine_id, b"", salt)


def create_fernet_cipher(fallback_file: Optional[Path] = None) -> "Fernet":
    """
    Instancie un cipher Fernet prêt à l'emploi.
    """
    key = get_or_create_fernet_key(fallback_file=fallback_file)
    return Fernet(key)


def encrypt_text(plaintext: str, fallback_file: Optional[Path] = None) -> str:
    """
    Chiffre une chaîne et retourne un token encodé en UTF-8.
    """
    cipher = create_fernet_cipher(fallback_file=fallback_file)
    return cipher.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_text(token: str, fallback_file: Optional[Path] = None) -> str:
    """
    Déchiffre un token Fernet et retourne la chaîne en clair.
    """
    cipher = create_fernet_cipher(fallback_file=fallback_file)
    return cipher.decrypt(token.encode("utf-8")).decode("utf-8")
