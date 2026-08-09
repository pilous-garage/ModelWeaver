"""Graphe de circulation des tâches (Taskflow) du swarm.

Déduit du graphe Team(Agent) → taskflow :
  - chaque type d'agent (rôle) CONSOMME un type de tâche (role_required qu'il
    pioche via task_claim_next, avec hiérarchie senior/mid/junior)
  - chaque type d'agent GÉNÈRE des types de tâches (champ `generates` du
    .agent.yaml) — ex. l'analyste découpe une issue en tâches

Le graphe se construit dans les 2 sens :
  1. Agents → graphe : role_type + ROLE_TO_TASK (consommation) + generates
  2. Graphe → agents : une issue génère des tâches → _wake_for_tasks réveille
     les agents capables du role_required (déjà câblé)
"""

from typing import Any, Dict, List


def _load_role_config(role: str, agent_name: str = "") -> Dict[str, Any]:
    """Charge le config .agent.yaml du rôle (generates) — best-effort.

    Si l'agent est un membre d'une team (nom `team:X/nom`), on cherche d'abord
    la référence catalogue (`ref` du manifest) pour charger LE bon .agent.yaml
    (ex. greedy-coder au lieu de codeur@v2). Sinon, chargement par rôle.
    """
    ref = ""
    if "/" in agent_name and agent_name.startswith("team:"):
        try:
            from services.team_spec import TeamSpec
            team = agent_name.split("/")[0][len("team:"):]
            spec = TeamSpec.from_yaml(f"services/manifests/teams/{team}.team.yaml")
            member = next((m for m in spec.members
                           if m.agent_name == agent_name.split("/", 1)[1]), None)
            if member and getattr(member, "ref", ""):
                ref = member.ref
        except Exception:
            ref = ""
    try:
        from services.api.catalogue_agents import _load_agent_yaml_config
        cfg = _load_agent_yaml_config(role, agent_name, catalogue_ref=ref) or {}
        return cfg
    except Exception:
        return {}


def _compatible_roles(role_required: str) -> List[str]:
    """Rôles compatibles (hiérarchie senior/mid/junior)."""
    try:
        from modules.sql.workspace import _compatible_roles as _cr
        return _cr(role_required)
    except Exception:
        return [role_required] if role_required else []


def _load_picked_task_types(cfg: Dict[str, Any]) -> List[str]:
    """Task_types PIOCHÉS par l'agent : extraits du step `pick` du FSM
    (token_task_pick task_types=[{type, max_difficulty}])."""
    types = []
    try:
        steps = ((cfg.get("entrypoints") or {}).get("main") or {}).get("steps") or []
        for s in steps:
            if s.get("fn") == "workspace/token_task_pick@v1":
                raw = ((s.get("inputs") or {}).get("task_types") or "")
                import re
                for m in re.finditer(r"type:\s*([\w]+)", raw):
                    if m.group(1) not in types:
                        types.append(m.group(1))
                break
    except Exception:
        pass
    return types


def build_taskflow(team_name: str = "") -> Dict[str, Any]:
    """Graphe taskflow d'une team (ou toutes les teams).

    Retourne :
      {
        "nodes": [{id, label, role_type, consumes: [roles], generates: [roles]}],
        "edges": [{from, to, kind: "consume"|"generate"}],
        "task_types": {type: {consumers: [...], producers: [...]}}
      }
    """
    from modules.sql.db import AgentsDB
    db = AgentsDB()
    try:
        if team_name:
            prefix = team_name + "/"
            rows = db.conn.execute(
                "SELECT agent_id, name, role_type FROM agents "
                "WHERE name LIKE ? AND name NOT LIKE ?",
                (prefix + "%", "%/swarm-orchestrator")).fetchall()
        else:
            rows = db.conn.execute(
                "SELECT agent_id, name, role_type FROM agents "
                "WHERE name NOT LIKE '%/swarm-orchestrator'").fetchall()
        agents = [dict(r) for r in rows]
    finally:
        db.close()

    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    task_types: Dict[str, Dict[str, Any]] = {}

    seen_roles: Dict[str, str] = {}
    for a in agents:
        role_type = a["role_type"] or "worker"

        # Génération : champ `generates` du .agent.yaml (via la ref du membre
        # de team si présent, sinon par rôle).
        cfg = _load_role_config(role_type, a["name"])
        generates = list(cfg.get("generates") or [])
        # Consommation : les task_types PIOCHÉS par le FSM (step pick du
        # .agent.yaml, token_task_pick). Un agent sans pick (ex. chat-pilot
        # non-greedy) ne consomme aucun token.
        consumes = _load_picked_task_types(cfg) or []
        # Un agent qui ne pioche NI ne génère de token (chat-pilot orchestrateur,
        # tmp-member…) n'appartient pas au taskflow : on le saute.
        if not consumes and not generates:
            continue

        # Un nœud par type de rôle (fusionner les agents du même type)
        if role_type not in seen_roles:
            seen_roles[role_type] = a["name"]
            nodes.append({
                "id": role_type,
                "label": role_type,
                "role_type": role_type,
                "agents": [a["name"]],
                "consumes": consumes,
                "generates": generates,
            })
        else:
            for n in nodes:
                if n["id"] == role_type:
                    n["agents"].append(a["name"])
                    # Union des generates (plusieurs agents du type)
                    for g in generates:
                        if g not in n["generates"]:
                            n["generates"].append(g)
                    for c in consumes:
                        if c not in n["consumes"]:
                            n["consumes"].append(c)
                    break

        # Arêtes + index des types de tâches
        for g in generates:
            edges.append({"from": role_type, "to": g, "kind": "generate"})
            task_types.setdefault(g, {"consumers": [], "producers": []})
            if role_type not in task_types[g]["producers"]:
                task_types[g]["producers"].append(role_type)
        for c in consumes:
            edges.append({"from": c, "to": role_type, "kind": "consume"})
            task_types.setdefault(c, {"consumers": [], "producers": []})
            if role_type not in task_types[c]["consumers"]:
                task_types[c]["consumers"].append(role_type)

    return {
        "team": team_name or "*",
        "nodes": nodes,
        "edges": edges,
        "task_types": task_types,
    }
