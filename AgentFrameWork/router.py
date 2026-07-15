"""Résolution de routes à runtime pour le framework d'agents.

Les agents sont dynamiques (rôles, capacités, états, droits d'accès). Les
routes ne sont PAS hardcodées au démarrage : elles sont dérivées à chaque
requête de (rôle -> skills/capabilities) × (instances live × état).

C'est le cœur du « Agent Framework Daemon » : au lieu d'enregistrer des
routes statiques, le daemon interroge ce routeur pour savoir quelles
opérations un agent donné expose *maintenant* (introspection + dispatch).
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple


@dataclass
class RouteSpec:
    op: str
    method: str
    kind: str            # "capability" | "lifecycle"
    summary: str
    requires_stream: bool = False
    skill: Optional[str] = None

    def to_dict(self) -> Dict[str, str]:
        return {
            "op": self.op,
            "method": self.method,
            "kind": self.kind,
            "summary": self.summary,
            "requires_stream": self.requires_stream,
            "skill": self.skill,
        }


# skill `chat` -> op dédiée (historique multi-tour via chat_turn).
_CHAT_OP = RouteSpec(
    "chat", "POST", "capability",
    "Tour de chat multi-turn (historique persisté dans variables_json.messages)",
    requires_stream=True, skill="chat",
)

# ops lifecycle (disponibilité dépendante de l'état de l'agent).
_LIFECYCLE_OPS = {
    "status":    RouteSpec("status", "GET", "lifecycle", "État courant de l'agent"),
    "configure": RouteSpec("configure", "POST", "lifecycle", "Reconfigure (temperature/max_tokens)"),
    "pause":     RouteSpec("pause", "POST", "lifecycle", "Met l'agent en pause"),
    "resume":    RouteSpec("resume", "POST", "lifecycle", "Reprend un agent en pause"),
    "kill":      RouteSpec("kill", "POST", "lifecycle", "Interrompt l'agent (signal kill)"),
}

# états autorisant chaque op lifecycle (None = toujours autorisé).
_LIFECYCLE_STATE_GATE = {
    "status": None,
    "configure": None,
    "pause": {"RUNNING"},
    "resume": {"PAUSED"},
    "kill": {"RUNNING", "PAUSED"},
}


def _skills_for_role(role: str) -> List[str]:
    if not role:
        return []
    try:
        from AgentsCatalogue.role_manager import RoleManager
        rd = RoleManager().get_role(role)
        return list(rd.skills) if rd else []
    except Exception:
        return []


def capability_ops_for(skills: List[str]) -> List[RouteSpec]:
    """Une route par skill de rôle : l'agent expose exactement les capacités
    déclarées par son rôle. `chat` est spécial (historique multi-tour) ; les
    autres skills se résolvent vers l'exécution générique du framework."""
    out: List[RouteSpec] = []
    for s in skills:
        if s == "chat":
            out.append(_CHAT_OP)
        else:
            out.append(RouteSpec(
                s, "POST", "capability",
                f"Exécution capacité '{s}' (contexte du rôle)",
                requires_stream=True, skill=s))
    return out


def routes_for(role: str, state: str, skills: Optional[List[str]] = None) -> List[RouteSpec]:
    """Liste les routes qu'un agent (rôle + état) expose MAINTENANT."""
    if skills is None:
        skills = _skills_for_role(role)
    routes = capability_ops_for(skills)
    for op, spec in _LIFECYCLE_OPS.items():
        gate = _LIFECYCLE_STATE_GATE.get(op)
        if gate is None or state in gate:
            routes.append(spec)
    return routes


def resolve(agent: Dict[str, str], op: str) -> Tuple[Optional[RouteSpec], Optional[str]]:
    """Renvoie (RouteSpec, None) si `op` est autorisée pour l'agent,
    sinon (None, raison) avec raison ∈ {unknown, not_capable, state}."""
    role = agent.get("role_type") or agent.get("role") or ""
    state = agent.get("status") or "INIT"
    skills = _skills_for_role(role)

    # lifecycle ?
    if op in _LIFECYCLE_OPS:
        gate = _LIFECYCLE_STATE_GATE.get(op)
        if gate is not None and state not in gate:
            return None, "state"
        return _LIFECYCLE_OPS[op], None

    # capability ?
    allowed = capability_ops_for(skills)
    for r in allowed:
        if r.op == op:
            return r, None
    # op connue (skill valide) mais pas accordée à CE rôle -> 403 not_capable
    try:
        from AgentsCatalogue import role_manager as _rm
        valid = _rm.VALID_SKILLS
    except Exception:
        valid = set()
    if op in valid or op == "chat":
        return None, "not_capable"
    return None, "unknown"


def capabilities_catalog() -> Dict[str, object]:
    """Catalogue des rôles + skills/capabilities déclarés (AgentsCatalogue)."""
    try:
        from AgentsCatalogue.role_manager import RoleManager
        from AgentsCatalogue import role_manager as _rm
        rm = RoleManager()
        roles: Dict[str, object] = {}
        for name in rm.list_roles():
            rd = rm.get_role(name)
            if not rd:
                continue
            roles[name] = {
                "description": rd.description,
                "skills": rd.skills,
                "model_requirements": rd.model_requirements,
                "contexts": rd.contexts,
                "default_config": rd.default_config,
            }
        return {
            "roles": roles,
            "valid_skills": sorted(_rm.VALID_SKILLS),
            "valid_capabilities": sorted(_rm.VALID_CAPABILITIES),
        }
    except Exception as e:
        return {"error": str(e)}
