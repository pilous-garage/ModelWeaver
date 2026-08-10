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


def op_yaml_to_taskflow(params: dict) -> Dict[str, Any]:
    """graphe_utile/yaml_to_taskflow — Pétri PLAT d'un agent (places, transitions,
    boxes, num_id) + folding zip/unzip côté backend.

    params : { name } | { yaml } | { data } | { action: "fold"/"unfold"/"fold_all",
             node: num_id }.
    """
    from modules.agent_graph_utils.taskflow import yaml_to_taskflow
    from modules.agent_graph_utils import petri as P

    data = _load_agent(params)
    if not data:
        return {"status": "error", "error": "agent introuvable (name/yaml/data requis)"}
    try:
        g = yaml_to_taskflow(data)
        net = P.PetriNet()
        net.by_id = {n["id"]: n["num_id"] for n in g["nodes"]}
        net._edge_counter = len(g["edges"])
        for n in g["nodes"]:
            net.nodes.append(P.PetriNode(num_id=n["num_id"], id=n["id"],
                                         class_=n["class"], specialty=n["specialty"],
                                         type=n["type"], label=n["label"],
                                         parent=n["parent"], vars=n["vars"]))
        for e in g["edges"]:
            net.edges.append(P.PetriEdge(num_id=e["num_id"], from_=e["from"], to=e["to"],
                                         type=e["type"], label=e["label"]))
        P.rebuild_groups(net)
        for _n in net.nodes:
            P.recheck(net, _n.num_id)
        action = params.get("action")
        node = params.get("node")
        if action == "fold" and node is not None:
            n = net.node(int(node))
            if n.foldable == "seq":
                P.fold_seq(net, int(node))
            elif n.foldable == "par":
                nums = [n.num_id]
                key = (tuple(sorted(n.in_group)), tuple(sorted(n.out_group)))
                for i in n.in_group:
                    for m in net.node(i).out_group:
                        c = net.node(m)
                        if c.num_id != n.num_id and c.foldable == "par":
                            if (tuple(sorted(c.in_group)), tuple(sorted(c.out_group))) == key and c.num_id not in nums:
                                nums.append(c.num_id)
                P.fold_par(net, nums)
            elif n.foldable == "single":
                P.fold_single(net, int(node))
        elif action == "unfold" and node is not None:
            P.unzip(net, int(node))
        elif action == "fold_all":
            guard = 0
            while guard < 500:
                found = P.find_foldable(net)
                if not found:
                    break
                n = net.node(found[0])
                if n.foldable == "seq":
                    P.fold_seq(net, found[0])
                elif n.foldable == "par":
                    key = (tuple(sorted(n.in_group)), tuple(sorted(n.out_group)))
                    nums = [n.num_id]
                    for i in n.in_group:
                        for m in net.node(i).out_group:
                            c = net.node(m)
                            if c.num_id != n.num_id and c.foldable == "par" and (tuple(sorted(c.in_group)), tuple(sorted(c.out_group))) == key and c.num_id not in nums:
                                nums.append(c.num_id)
                    P.fold_par(net, nums)
                elif n.foldable == "single":
                    P.fold_single(net, found[0])
                guard += 1
        # comptes visibles
        counts = net.count_visible()
        return {"status": "ok", "title": g["title"], "action": action or "none",
                "counts": counts,
                "nodes": [{"num_id": n.num_id, "id": n.id, "class": n.class_,
                           "specialty": n.specialty, "type": n.type, "label": n.label,
                           "parent": n.parent, "visible": n.visible, "zipped": n.zipped,
                           "foldable": n.foldable, "in_group": n.in_group,
                           "out_group": n.out_group, "anciens": n.anciens,
                           "vars": n.vars}
                          for n in net.nodes],
                "edges": [{"num_id": e.num_id, "from": e.from_, "to": e.to,
                           "type": e.type, "label": e.label, "visible": e.visible}
                          for e in net.edges]}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


register("graphe_utile/yaml_to_fsm", op_yaml_to_fsm)
register("graphe_utile/yaml_to_taskflow", op_yaml_to_taskflow)
