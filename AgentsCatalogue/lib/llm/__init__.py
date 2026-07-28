"""LLM — Appels LLM, outils, résolution, permissions et boucle autonome."""

from .tool import Def, Info, Registry, define, get_registry, load_tool_registry
from .resolver import resolve_tools, make_dispatcher
from .loop import run, LoopConfig, LoopResult, Turn
from .permission import Action, Rule, Ruleset, PermissionChecker
from .call import llm_call
from .bridge import capabilities, list_providers, list_models, health
from .model import llm_model

__all__ = [
    'Def', 'Info', 'Registry', 'define', 'get_registry', 'load_tool_registry',
    'resolve_tools', 'make_dispatcher',
    'run', 'LoopConfig', 'LoopResult', 'Turn',
    'Action', 'Rule', 'Ruleset', 'PermissionChecker',
    'llm_call', 'capabilities', 'list_providers', 'list_models', 'health',
    'llm_model',
]


def register_default_tools(registry: Registry) -> None:
    """Alias — délègue à ``load_tool_registry()`` (auto-découverte YAML)."""
    load_tool_registry(registry)


__builtins__ = {
    "register_default_tools": register_default_tools,
}
