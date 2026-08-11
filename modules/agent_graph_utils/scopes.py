#!/usr/bin/env python3
"""scopes — Pile de scopes hiérarchiques pour la résolution de variables.

Le langage a des niveaux de déclaration de variables (agent, skill, subskill,
profondeur ARBITRAIRE). Une pile de scopes est maintenue par le FSMInterpreter,
poussée à chaque skill/subskill.

Règles :
  - Écriture SANS préfixe : `var_x = x` → scope COURANT (le plus haut de la
    pile = là où s'exécute le step).
  - Écriture AVEC préfixe : `agent.var_x = x` → scope nommé (`agent`),
    remonté depuis le courant ; si absent → erreur.
  - Lecture `$var_x` : remonte la pile (feuille → racine) → la première
    valeur trouvée gagne. Résolue au niveau du STEP (avant inline/call).
  - `$scope.var_x` : lecture ciblée sur un scope nommé précis.

Syntaxe :
  - `$var` / `$scope.var` dans les fn/inputs (résolu par _resolve_vars).
  - `var = x` / `agent.var = x` dans les steps set_variable / les assignments
    des foncteurs.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

_VAR_REF = re.compile(r"\$([\w.]+)")
_ASSIGN_REF = re.compile(r"^([\w.]+?)\s*=\s*(.*)$")


class ScopeError(KeyError):
    pass


class ScopeStack:
    """Pile de scopes : le dernier élément est le scope COURANT (feuille)."""

    def __init__(self) -> None:
        self._scopes: List[Dict[str, Any]] = []
        self._names: List[str] = []

    # ── gestion de la pile ────────────────────────────────

    def push(self, name: str = "", vars: Optional[Dict[str, Any]] = None) -> None:
        """Pousse un scope (ex. "skill:read_file" ou "subskill")."""
        self._scopes.append(dict(vars or {}))
        self._names.append(name)

    def pop(self) -> Optional[Dict[str, Any]]:
        if not self._scopes:
            return None
        self._names.pop()
        return self._scopes.pop()

    @property
    def depth(self) -> int:
        return len(self._scopes)

    @property
    def current(self) -> Dict[str, Any]:
        """Scope courant (feuille). Vide si pile vide."""
        return self._scopes[-1] if self._scopes else {}

    def current_name(self) -> str:
        return self._names[-1] if self._names else "agent"

    # ── écriture ──────────────────────────────────────────

    def set(self, key: str, value: Any) -> None:
        """Écrit dans le scope COURANT (feuille). `var_x = x`."""
        self.current[key] = value

    def set_scoped(self, path: str, value: Any) -> None:
        """Écrit avec préfixe de scope : `agent.var_x = x`.

        Le préfixe (ex. `agent`) est remonté depuis le courant ; s'il n'existe
        pas → erreur. Le DERNIER segment est la clé."""
        parts = path.split(".")
        if len(parts) == 1:
            self.set(parts[0], value)
            return
        scope_name, key = ".".join(parts[:-1]), parts[-1]
        idx = self._find_scope(scope_name)
        if idx < 0:
            raise ScopeError(f"scope '{scope_name}' introuvable (hiérarchie: "
                             f"{self._names or ['agent']})")
        self._scopes[idx][key] = value

    def _find_scope(self, name: str) -> int:
        """Index du scope nommé `name`, remonté depuis la feuille."""
        for i in range(len(self._scopes) - 1, -1, -1):
            if self._names[i] == name:
                return i
        return -1

    # ── lecture hiérarchique ──────────────────────────────

    def get(self, key: str) -> Any:
        """Lecture en remontant la pile (feuille → racine)."""
        for scope in reversed(self._scopes):
            if key in scope:
                return scope[key]
        raise ScopeError(f"variable '${key}' introuvable (scopes: "
                         f"{self._names or ['agent']})")

    def get_scoped(self, path: str) -> Any:
        """Lecture ciblée : `$agent.var_x` → scope agent, clé var_x."""
        parts = path.split(".")
        if len(parts) == 1:
            return self.get(parts[0])
        idx = self._find_scope(".".join(parts[:-1]))
        if idx < 0:
            raise ScopeError(f"scope '{'.'.join(parts[:-1])}' introuvable")
        return self._scopes[idx][parts[-1]]

    # ── résolution $var dans une chaîne ───────────────────

    def resolve_text(self, text: str) -> str:
        """Remplace `$var` / `$scope.var` dans une chaîne par leurs valeurs."""
        if not isinstance(text, str) or "$" not in text:
            return text

        def _repl(m):
            path = m.group(1)
            try:
                return str(self.get_scoped(path))
            except ScopeError:
                return m.group(0)   # laisse le placeholder si introuvable

        return _VAR_REF.sub(_repl, text)

    def resolve_value(self, value: Any) -> Any:
        """Résout récursivement les $var dans un objet (str, dict, list)."""
        if isinstance(value, str):
            return self.resolve_text(value)
        if isinstance(value, dict):
            return {k: self.resolve_value(v) for k, v in value.items()}
        if isinstance(value, list):
            return [self.resolve_value(v) for v in value]
        return value

    def snapshot(self) -> Dict[str, Any]:
        """Fusion hiérarchique (feuille écrase racine) → dict plat."""
        out: Dict[str, Any] = {}
        for scope in self._scopes:
            out.update(scope)
        return out
