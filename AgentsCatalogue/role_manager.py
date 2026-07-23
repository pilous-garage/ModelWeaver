"""RoleManager — Chargement et validation des définitions de rôles YAML.

Chaque rôle est un fichier YAML avec ce schéma :

```yaml
name: str                    # Identifiant unique du rôle
description: str             # Description courte
system_prompt: str           # Prompt système définissant le comportement
skills: list[str]            # Compétences : code_gen, review, debug, refactor, research, plan, doc, qa, orchestrate, monitor
model_requirements:          # Contraintes sur le modèle
  min_context_tokens: int    # Contexte minimum requis
  capabilities: list[str]    # text_generation, code_generation, vision, reasoning
contexts: list[str]          # Contextes de travail (ex: "general", "project:modelweaver")
default_config:              # Configuration par défaut
  temperature: float
  max_tokens: int
```
"""

import json
import yaml
from pathlib import Path
from typing import Any, Dict, List, Optional


ROLES_DIR = Path(__file__).resolve().parent / "rôles"

VALID_SKILLS = {
    "chat", "code_gen", "review", "debug", "refactor",
    "research", "plan", "doc", "qa", "orchestrate", "monitor",
    "architect", "critique", "summarize", "search",
}

VALID_CAPABILITIES = {
    "text_generation", "code_generation", "vision", "reasoning", "audio",
}


class RoleValidationError(ValueError):
    pass


class RoleDefinition:
    """Définition chargée d'un rôle."""

    def __init__(self, data: Dict[str, Any]):
        self.name: str = data.get("name", "unknown")
        self.description: str = data.get("description", "")
        self.system_prompt: str = data.get("system_prompt", "")
        self.skills: List[str] = data.get("skills", data.get("allowed_skills", []))
        self.model_requirements: Dict[str, Any] = data.get("model_requirements", {})
        self.contexts: List[str] = data.get("contexts", ["general"])
        self.default_config: Dict[str, Any] = data.get("default_config", {})
        self.raw: Dict[str, Any] = data

    @classmethod
    def from_file(cls, path: Path) -> "RoleDefinition":
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return cls(data or {})

    def validate(self) -> List[str]:
        errors = []
        if not self.name:
            errors.append("name requis")
        if not self.system_prompt:
            errors.append("system_prompt requis")
        for s in self.skills:
            if s not in VALID_SKILLS:
                errors.append(f"skill inconnu: {s} (valides: {', '.join(sorted(VALID_SKILLS))})")
        req = self.model_requirements
        if req:
            for cap in req.get("capabilities", []):
                if cap not in VALID_CAPABILITIES:
                    errors.append(f"capabilité inconnue: {cap}")
        return errors

    def classification_str(self) -> str:
        cls = self.raw.get("classification", {})
        if not cls:
            return "uncategorized"
        parts = [cls.get("class", "")]
        if cls.get("sub_class"):
            parts.append(cls["sub_class"])
        return "/".join(parts)

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "name": self.name,
            "description": self.description,
            "system_prompt": self.system_prompt,
            "skills": self.skills,
            "model_requirements": self.model_requirements,
            "contexts": self.contexts,
            "default_config": self.default_config,
        }
        classification = self.raw.get("classification")
        if classification:
            result["classification"] = classification
        pipeline = self.raw.get("pipeline")
        if pipeline:
            result["pipeline"] = pipeline
        return result


class RoleManager:
    """Gère le registre des rôles disponibles."""

    def __init__(self, roles_dir: Optional[Path] = None):
        self.roles_dir = roles_dir or ROLES_DIR
        self.roles_dir.mkdir(parents=True, exist_ok=True)
        self._cache: Dict[str, RoleDefinition] = {}

    def list_roles(self) -> List[str]:
        self._refresh_cache()
        return sorted(self._cache.keys())

    def get_role(self, name: str) -> Optional[RoleDefinition]:
        self._refresh_cache()
        return self._cache.get(name)

    def get_system_prompt(self, name: str) -> str:
        role = self.get_role(name)
        return role.system_prompt if role else ""

    def get_skills(self, name: str) -> List[str]:
        role = self.get_role(name)
        return role.skills if role else []

    def save_role(self, definition: RoleDefinition) -> Path:
        errors = definition.validate()
        if errors:
            raise RoleValidationError(f"Rôle '{definition.name}' invalide: {'; '.join(errors)}")
        path = self.roles_dir / f"{definition.name}.role.yaml"
        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(definition.to_dict(), f, default_flow_style=False, allow_unicode=True)
        self._cache[definition.name] = definition
        return path

    def delete_role(self, name: str) -> bool:
        path = self.roles_dir / f"{name}.role.yaml"
        if path.exists():
            path.unlink()
            self._cache.pop(name, None)
            return True
        return False

    def _refresh_cache(self):
        for path in sorted(self.roles_dir.glob("*.role.yaml")):
            if path.stem not in self._cache:
                try:
                    role = RoleDefinition.from_file(path)
                    errors = role.validate()
                    if errors:
                        print(f"  ⚠️  Rôle {path.name}: {'; '.join(errors)}")
                    else:
                        self._cache[path.stem] = role
                except Exception as e:
                    print(f"  ⚠️  Rôle {path.name}: erreur ({e})")

    def to_json(self) -> str:
        return json.dumps({k: v.to_dict() for k, v in self._cache.items()}, indent=2)
