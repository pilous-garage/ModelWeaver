"""RoleManager — Chargement et validation des définitions de rôles YAML.

Chaque rôle est un fichier YAML décrivant :
  - system_prompt: le prompt système qui définit le comportement
  - allowed_skills: liste des skills que le rôle peut exécuter
  - model_requirements: contraintes sur le modèle (contexte mini, capacités...)
  - default_config: configuration par défaut
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


ROLES_DIR = Path(__file__).resolve().parent / "roles"


class RoleDefinition:
    """Définition chargée d'un rôle."""

    def __init__(self, data: Dict[str, Any]):
        self.name: str = data.get("name", "unknown")
        self.description: str = data.get("description", "")
        self.system_prompt: str = data.get("system_prompt", "")
        self.allowed_skills: List[str] = data.get("allowed_skills", [])
        self.model_requirements: Dict[str, Any] = data.get("model_requirements", {})
        self.default_config: Dict[str, Any] = data.get("default_config", {})
        self.raw: Dict[str, Any] = data

    @classmethod
    def from_file(cls, path: Path) -> "RoleDefinition":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data or {})

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "allowed_skills": self.allowed_skills,
            "model_requirements": self.model_requirements,
            "default_config": self.default_config,
        }


class RoleManager:
    """Gère le registre des rôles disponibles."""

    def __init__(self, roles_dir: Optional[Path] = None):
        self.roles_dir = roles_dir or ROLES_DIR
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, RoleDefinition] = {}

    def list_roles(self) -> List[str]:
        """Liste les noms de rôles disponibles."""
        self._refresh_cache()
        return sorted(self._cache.keys())

    def get_role(self, name: str) -> Optional[RoleDefinition]:
        """Charge un rôle par son nom."""
        self._refresh_cache()
        return self._cache.get(name)

    def get_system_prompt(self, name: str) -> str:
        """Retourne le system prompt d'un rôle, ou chaîne vide."""
        role = self.get_role(name)
        return role.system_prompt if role else ""

    def get_allowed_skills(self, name: str) -> List[str]:
        role = self.get_role(name)
        return role.allowed_skills if role else []

    def save_role(self, definition: RoleDefinition) -> Path:
        """Sauvegarde ou écrase un fichier de rôle."""
        path = self.roles_dir / f"{definition.name}.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(definition.to_dict(), f, default_flow_style=False, allow_unicode=True)
        self._cache[definition.name] = definition
        return path

    def delete_role(self, name: str) -> bool:
        path = self.roles_dir / f"{name}.yaml"
        if path.exists():
            path.unlink()
            self._cache.pop(name, None)
            return True
        return False

    def _refresh_cache(self):
        for path in self.roles_dir.glob("*.yaml"):
            if path.stem not in self._cache:
                try:
                    self._cache[path.stem] = RoleDefinition.from_file(path)
                except Exception as e:
                    print(f"  ⚠️  Rôle {path.name}: erreur de chargement ({e})")

    def to_json(self) -> str:
        return json.dumps({k: v.to_dict() for k, v in self._cache.items()}, indent=2)
