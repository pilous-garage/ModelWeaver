"""Routes graphe_utile/* — utilitaires de graphe (module agent_graph_utils).

Le GUI v2 consomme ces routes pour construire les graphes FSM/Pétri : logique
PURE (aucun thème, aucune position). Les actions COMPLEXES (fold Petri, etc.)
seront renvoyées sous forme de "modif" (add/remove/modify) que le GUI applique
sur son graphe mathématique local.

  graphe_utile/yaml_to_fsm : agent YAML → graphe FSM hiérarchique
                             (nœuds + arêtes, tags hérités, inner foldable).
"""

from typing import Any, Dict

from services.api.router import register


def _load_agent(params: dict) -> Dict[str, Any]:
    """Retourne le `data` agent (dict) depuis params.data / params.yaml / name."""
    data = params.get("data")
    if data is not None:
        return data
    yaml_str = params.get("yaml")
    if yaml_str:
        import yaml
        parsed = yaml.safe_load(yaml_str)
        return parsed if isinstance(parsed, dict) else None
    name = params.get("name")
    if name:
        from services.api.handlers.agents import op_catalogue_agents_get
        res = op_catalogue_agents_get({"name": name})
        if res.get("status") == "ok" and res.get("data"):
            return res["data"]
    return None


def op_yaml_to_fsm(params: dict) -> Dict[str, Any]:
    """graphe_utile/yaml_to_fsm — construit le graphe FSM d'un agent.

    params : { name } (chargé du catalogue) | { yaml } | { data: dict }.
    Retourne {nodes, edges, title} — logique pure, sans thème ni positions.
    """
    from modules.agent_graph_utils.fsm import yaml_to_fsm

    data = _load_agent(params)
    if not data:
        return {"status": "error", "error": "agent introuvable (name/yaml/data requis)"}
    try:
        g = yaml_to_fsm(data)
        g["status"] = "ok"
        return g
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


register("graphe_utile/yaml_to_fsm", op_yaml_to_fsm)
