"""ToolExecutor — Exécuteur de fonctions pour les agents.

Permet aux agents d'interagir avec le système de fichiers et le shell.
"""

import subprocess
import os
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("modelweaver.tool_executor")


class ToolExecutor:
    """Exécute des outils système sécurisés."""

    def __init__(self, workspace_root: str):
        self.workspace_root = workspace_root

    def execute(self, tool_name: str, args: Dict[str, Any]) -> str:
        """Dispatch vers la fonction outil correspondante."""
        method = getattr(self, f"_tool_{tool_name}", None)
        if not method:
            return f"Erreur : L'outil '{tool_name}' n'existe pas."
        
        try:
            return method(**args)
        except Exception as e:
            logger.exception("Erreur lors de l'exécution de l'outil %s", tool_name)
            return f"Erreur lors de l'exécution de l'outil : {str(e)}"

    def _tool_read_file(self, path: str) -> str:
        """Lit le contenu d'un fichier."""
        full_path = self._safe_path(path)
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()

    def _tool_write_file(self, path: str, content: str) -> str:
        """Écrit le contenu dans un fichier."""
        full_path = self._safe_path(path)
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        return f"Fichier {path} écrit avec succès."

    def _tool_run_shell(self, command: str) -> str:
        """Exécute une commande shell et retourne la sortie."""
        # ATTENTION : Très dangereux, normalement restreint à un sandbox
        try:
            result = subprocess.run(
                command, 
                shell=True, 
                capture_output=True, 
                text=True, 
                timeout=30,
                cwd=self.workspace_root
            )
            return f"STDOUT:\n{result.stdout}\nSTDERR:\n{result.stderr}"
        except subprocess.TimeoutExpired:
            return "Erreur : Timeout de la commande (30s)."
        except Exception as e:
            return f"Erreur d'exécution : {str(e)}"

    def _safe_path(self, path: str) -> str:
        """Empêche la sortie du répertoire racine (Path Traversal)."""
        # Nettoyage basique
        normalized = os.path.normpath(path)
        if normalized.startswith("..") or normalized.startswith("/"):
            # On force le chemin relatif au workspace
            normalized = normalized.lstrip("/")
        
        full_path = os.path.join(self.workspace_root, normalized)
        if not full_path.startswith(os.path.abspath(self.workspace_root)):
            raise PermissionError("Accès refusé : Tentative de sortir du workspace.")
        
        return full_path
