import os
import requests
from pathlib import Path
from typing import Optional

class GitHubBridge:
    """Gère l'envoi de recettes vers le dépôt GitHub officiel."""
    
    def __init__(self):
        self.token = os.getenv("GITHUB_TOKEN")
        self.repo = "pilous-garage/ModelWeaver"
        self.branch = "main"
        self.api_url = f"https://api.github.com/repos/{self.repo}/contents"

    def push_recipe(self, relative_path: Path, content: str, message: str = "Add/Update recipe") -> bool:
        """Pousse un fichier YAML sur GitHub."""
        if not self.token:
            print("❌ GITHUB_TOKEN manquant dans .env")
            return False
        
        url = f"{self.api_url}/{relative_path}"
        
        # Vérifier si le fichier existe pour récupérer le SHA (requis pour update)
        sha = None
        try:
            res = requests.get(url, headers={"Authorization": f"token {self.token}"})
            if res.status_code == 200:
                sha = res.json().get("sha")
        except Exception:
            pass

        data = {
            "message": message,
            "content": content,
            "sha": sha
        }
        
        try:
            res = requests.put(url, json=data, headers={"Authorization": f"token {self.token}"})
            return res.status_code in (200, 201)
        except Exception as e:
            print(f"❌ Erreur GitHub Push: {e}")
            return False
