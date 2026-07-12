import os
import base64
from pathlib import Path
from typing import Optional
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.backends import default_backend
import keyring
from dotenv import load_dotenv

load_dotenv()

class VaultLockedError(Exception):
    """Exception levée quand on tente d'accéder au Vault alors qu'il est verrouillé."""
    pass

class Vault:
    """Gère le chiffrement et le déchiffrement des données sensibles via un Master Password.
    
    L'accès est hybride : Trousseau système (Desktop) ou Saisie manuelle (Serveur).
    """
    
    def __init__(self):
        self._fernet: Optional[Fernet] = None
        self.salt_path = Path(".modelweaver/vault.salt").absolute()
        self._ensure_salt()

    def _ensure_salt(self):
        """S'assure qu'un sel existe pour la dérivation de la clé."""
        self.salt_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.salt_path.exists():
            salt = os.urandom(16)
            self.salt_path.write_bytes(salt)

    def _derive_key(self, password: str) -> bytes:
        """Dérive une clé AES-256 à partir d'un mot de passe et du sel."""
        salt = self.salt_path.read_bytes()
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
            backend=default_backend()
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode()))

    def unlock(self, password: Optional[str] = None) -> bool:
        """Déverrouille le coffre-fort.
        
        Tente d'abord le keyring, puis le mot de passe fourni.
        """
        # 1. Tentative via le trousseau système
        try:
            stored_key = keyring.get_password("modelweaver", "master_key")
            if stored_key:
                self._fernet = Fernet(stored_key.encode())
                return True
        except Exception:
            pass

        # 2. Tentative via mot de passe fourni
        if password:
            derived_key = self._derive_key(password)
            self._fernet = Fernet(derived_key)
            # On tente de sauvegarder la clé dérivée dans le keyring pour la prochaine fois
            try:
                keyring.set_password("modelweaver", "master_key", derived_key.decode())
            except Exception:
                pass
            return True

        return False

    def is_unlocked(self) -> bool:
        return self._fernet is not None

    def encrypt(self, data: str) -> str:
        if not self._fernet:
            raise RuntimeError("Le coffre-fort est verrouillé. Veuillez l'ouvrir avec unlock().")
        if not data: return data
        return self._fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token: return token
        if not self._fernet:
            # On lève une erreur pour signaler que le déverrouillage est requis
            raise VaultLockedError("Le coffre-fort est verrouillé. Veuillez fournir le mot de passe.")
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except Exception:
            # Migration: retour fallback si le token n'est pas chiffré
            return token

vault = Vault()
