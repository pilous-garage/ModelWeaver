import os
from cryptography.fernet import Fernet
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class Vault:
    """Gère le chiffrement et le déchiffrement des données sensibles."""
    
    def __init__(self):
        # On utilise une clé MASTER_KEY dans le .env. 
        # En production, cela serait dérivé d'un mot de passe utilisateur via PBKDF2.
        self.key = os.getenv("MASTER_KEY")
        if not self.key:
            # Génération d'une clé si absente pour éviter le crash, 
            # mais on avertit l'utilisateur.
            self.key = Fernet.generate_key().decode()
            print("⚠️  MASTER_KEY absente du .env. Une clé temporaire a été générée.")
        
        self.fernet = Fernet(self.key.encode())

    def encrypt(self, data: str) -> str:
        if not data: return data
        return self.fernet.encrypt(data.encode()).decode()

    def decrypt(self, token: str) -> str:
        if not token: return token
        try:
            return self.fernet.decrypt(token.encode()).decode()
        except Exception:
            # Si le déchiffrement échoue, on retourne la valeur telle quelle 
            # (cas des clés non encore chiffrées lors de la migration)
            return token

vault = Vault()
