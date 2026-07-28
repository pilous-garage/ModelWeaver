"""Permission — Règles d'accès aux outils par agent.

Chaque agent peut définir dans son YAML une section ``permissions``
qui liste les règles d'accès pour chaque outil ::

    permissions:
      - tool: "*"             # tous les outils
        action: allow
      - tool: "shell_exec"
        action: ask           # demande confirmation utilisateur
      - tool: "dangerous_op"
        action: deny

Trois actions :
  - ``allow`` : exécution sans confirmation
  - ``deny``  : refus silencieux (l'outil n'est pas proposé au LLM)
  - ``ask``   : demande de confirmation (si le canal le permet)

L'évaluation se fait par **last-match-wins** : la dernière règle qui
matche un outil l'emporte. Le défaut est ``ask``.

Inspiré de : opencode packages/opencode/src/permission/index.ts
"""

import fnmatch
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Set


class Action(Enum):
    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass
class Rule:
    tool: str               # nom ou pattern wildcard ("*", "read_*", "shell_*")
    action: Action


@dataclass
class Ruleset:
    rules: List[Rule] = field(default_factory=list)

    @classmethod
    def default(cls) -> "Ruleset":
        return cls(rules=[Rule(tool="*", action=Action.ASK)])

    @classmethod
    def all_allow(cls) -> "Ruleset":
        return cls(rules=[Rule(tool="*", action=Action.ALLOW)])

    @classmethod
    def from_dict(cls, data: list) -> "Ruleset":
        """Construit un Ruleset depuis une liste YAML.
        
        ``[{tool: "read*", action: "allow"}, {tool: "shell_rm", action: "deny"}]``
        """
        rules = []
        for item in data:
            tool = item.get("tool", "*")
            action_str = item.get("action", "ask")
            try:
                action = Action(action_str)
            except ValueError:
                action = Action.ASK
            rules.append(Rule(tool=tool, action=action))
        return cls(rules=rules)

    @staticmethod
    def _specificity(tool: str) -> int:
        """Ordre de spécificité : exact > wildcard > *."""
        if tool == "*":
            return 0
        if "*" in tool or "?" in tool:
            return 1
        return 2

    def evaluate(self, tool_name: str) -> Action:
        """Évalue la règle pour un outil donné (most-specific-first).

        Les règles exactes (ex: ``read``) priment sur les wildcards
        (ex: ``read_*``), qui priment sur ``*``.
        Les règles à égalité de spécificité sont évaluées dans l'ordre
        du YAML (last-match-wins parmi le même niveau).
        """
        rules_by_spec = sorted(
            self.rules,
            key=lambda r: self._specificity(r.tool),
        )
        matched = None
        for rule in rules_by_spec:
            if fnmatch.fnmatch(tool_name, rule.tool):
                matched = rule
        if matched is None:
            return Action.ASK
        return matched.action

    def is_allowed(self, tool_name: str) -> bool:
        return self.evaluate(tool_name) == Action.ALLOW

    def is_denied(self, tool_name: str) -> bool:
        return self.evaluate(tool_name) == Action.DENY

    def visible_tools(self, all_tools: Set[str]) -> Set[str]:
        """Filtre les outils : retire ceux qui sont deny."""
        return {t for t in all_tools if not self.is_denied(t)}


class PermissionChecker:
    """Vérifie les permissions avant exécution d'un outil.

    Intègre la notion de ``ask`` (confirmation utilisateur) et de
    ``deny`` (refus silencieux). La confirmation ``ask`` est déléguée
    à la fonction ``ask_handler`` passée à la construction.
    """

    def __init__(self, ruleset: Optional[Ruleset] = None,
                 ask_handler=None):
        self.ruleset = ruleset or Ruleset.default()
        self.ask_handler = ask_handler

    def check(self, tool_name: str,
              context: Optional[dict] = None) -> bool:
        """Vérifie si un outil peut être exécuté.

        Returns:
            True si autorisé, False si refusé (deny ou ask non approuvé).
        """
        action = self.ruleset.evaluate(tool_name)

        if action == Action.DENY:
            return False

        if action == Action.ALLOW:
            return True

        if action == Action.ASK:
            if self.ask_handler:
                return self.ask_handler(tool_name, context or {})
            return True     # pas de handler = laisser passer

        return True

    def merge(self, other: "PermissionChecker") -> "PermissionChecker":
        """Fusionne deux checkers : les règles de ``other`` prennent le dessus."""
        combined = self.ruleset.rules + other.ruleset.rules
        return PermissionChecker(
            ruleset=Ruleset(rules=combined),
            ask_handler=self.ask_handler or other.ask_handler,
        )
