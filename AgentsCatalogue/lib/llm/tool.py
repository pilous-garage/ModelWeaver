"""Tool — Contrat standardisé pour les outils LLM.

Chaque outil expose : nom, description, schéma des paramètres, exécution.

Inspiré de : opencode packages/opencode/src/tool/tool.ts
"""

import copy
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


@dataclass
class Def:
    """Définition complète d'un outil (après résolution)."""
    name: str
    description: str
    parameters: Dict[str, Any]      # JSON Schema
    execute: Callable               # (inputs: dict, ws: str) -> dict


@dataclass
class Info:
    """Inscription différée d'un outil (avant résolution)."""
    name: str
    description: str
    parameters: Dict[str, Any]      # JSON Schema
    fn: Callable                    # (inputs: dict, ws: str) -> dict
    injected: List[str] = field(default_factory=list)

    def resolve(self) -> Def:
        return Def(
            name=self.name,
            description=self.description,
            parameters=copy.deepcopy(self.parameters),
            execute=self.fn,
        )


def define(
    name: str,
    description: str = "",
    parameters: Optional[Dict[str, Any]] = None,
    injected: Optional[List[str]] = None,
):
    """Décorateur : enregistre une fonction comme outil.

    Usage::

        @tool.define("read_file", "Lit un fichier", {
            "path": {"type": "string", "description": "Chemin du fichier"}
        })
        def read_file(inputs, ws):
            ...
    """
    def wrapper(fn: Callable) -> Info:
        return Info(
            name=name,
            description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            fn=fn,
            injected=injected or [],
        )
    return wrapper


class Registry:
    """Registre d'outils : inscription + résolution + exécution.

    Peut optionnellement être associé à un ``PermissionChecker`` pour
    filtrer les outils selon les droits de l'agent.
    """

    def __init__(self, permission_checker=None):
        self._tools: Dict[str, Info] = {}
        self.permission_checker = permission_checker

    def add(self, info: Info) -> None:
        self._tools[info.name] = info

    def add_fn(self, name: str, fn: Callable,
               description: str = "",
               parameters: Optional[Dict[str, Any]] = None,
               injected: Optional[List[str]] = None) -> None:
        self._tools[name] = Info(
            name=name, description=description,
            parameters=parameters or {"type": "object", "properties": {}},
            fn=fn, injected=injected or [],
        )

    def get(self, name: str) -> Optional[Def]:
        info = self._tools.get(name)
        if info is None:
            return None
        return info.resolve()

    def list(self) -> List[Info]:
        return list(self._tools.values())

    def remove(self, name: str) -> None:
        self._tools.pop(name, None)

    def clear(self) -> None:
        self._tools.clear()

    def to_openai_tools(self, exclude: Optional[set] = None) -> List[Dict]:
        """Convertit tous les outils en format OpenAI function calling.

        Les outils marqués ``deny`` par le PermissionChecker sont exclus.
        Retourne une liste de dicts compatible OpenAI/Anthropic/Groq/Mistral::

            [{
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": {"type": "object", "properties": {...}}
                }
            }]
        """
        excl = exclude or set()
        result = []
        for info in self._tools.values():
            if info.name in excl:
                continue
            if self.permission_checker and self.permission_checker.ruleset.is_denied(info.name):
                continue
            params = copy.deepcopy(info.parameters)
            if "type" not in params or params.get("type") != "object":
                params = {"type": "object", "properties": params}
            param_props = params.get("properties", {})
            for key in info.injected:
                param_props.pop(key, None)
            if params.get("required"):
                params["required"] = [r for r in params["required"]
                                      if r not in info.injected]
            result.append({
                "type": "function",
                "function": {
                    "name": info.name,
                    "description": info.description[:200],
                    "parameters": params,
                },
            })
        return result

    def execute(self, name: str, inputs: dict, ws: str = "",
                permission_context: Optional[dict] = None) -> dict:
        """Exécute un outil par son nom.

        Vérifie les permissions avant exécution si un PermissionChecker
        est associé au registre.

        Args:
            name: Nom de l'outil
            inputs: Arguments (déjà parsés depuis JSON)
            ws: Workspace/home path
            permission_context: Contexte passé au checker pour la
                               décision ``ask``.

        Returns:
            Dictionnaire résultat (standardisé)
        """
        info = self._tools.get(name)
        if info is None:
            return {"ok": False, "error": f"Unknown tool: {name}"}

        if self.permission_checker:
            allowed = self.permission_checker.check(
                name, context=permission_context or {},
            )
            if not allowed:
                return {
                    "ok": False,
                    "error": f"Permission denied: {name}",
                    "permission": "deny",
                }

        try:
            result = info.fn(inputs, ws)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}


_registry = None
_TOOLS_DIR = Path(__file__).resolve().parent.parent.parent / "tools"
_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"


def _resolve_function(dotted: str) -> Callable:
    """Importe une fonction depuis un chemin dotted relatif à ``AgentsCatalogue.lib``.

    Exemple: ``file.ops.grep`` → ``AgentsCatalogue.lib.file.ops.grep``
    """
    import importlib
    parts = dotted.split(".")
    module_path = "AgentsCatalogue.lib." + ".".join(parts[:-1])
    func_name = parts[-1]
    mod = importlib.import_module(module_path)
    return getattr(mod, func_name)


def load_tool_registry(registry: Registry) -> None:
    """Auto-découvre les ``tools/*.tool.yaml`` et les enregistre dans le Registry.

    Idempotent : les outils déjà présents ne sont pas écrasés.
    """
    if not _TOOLS_DIR.exists():
        return
    for yaml_path in sorted(_TOOLS_DIR.glob("*.tool.yaml")):
        name = yaml_path.stem.replace(".tool", "")
        if registry.get(name) is not None:
            continue  # déjà enregistré
        try:
            import yaml as _yaml
            with open(yaml_path, encoding="utf-8") as f:
                data = _yaml.safe_load(f)
        except Exception:
            continue
        impl = data.get("implementation", {})
        fn_path = impl.get("function", "")
        if not fn_path:
            continue
        try:
            fn = _resolve_function(fn_path)
        except Exception:
            continue
        params = data.get("parameters", {"type": "object", "properties": {}})
        registry.add(Info(
            name=data["name"],
            description=data.get("description", ""),
            parameters=params,
            fn=fn,
            injected=impl.get("injected", []),
        ))


def get_registry() -> Registry:
    """Registry singleton, auto-alimenté depuis ``tools/*.tool.yaml``."""
    global _registry
    if _registry is None:
        _registry = Registry()
        load_tool_registry(_registry)
    return _registry
