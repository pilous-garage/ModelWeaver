"""Routes utils/* — service utilitaire central.

Structure du service (côté daemon) : ``utils/<namespace>/<fonction>``.

  utils/compile/yaml-inline : inline un yaml_simple (skill/agent/team) en
                              document self-contained (déclarations catalogue
                              remontées au plus haut niveau, intérieur résolu
                              récursivement). Le FSM ne reçoit QUE des inline.
  utils/graph/fsm          : graphe FSM hiérarchique depuis un agent INLINE.
  utils/graph/taskflow     : Pétri PLAT depuis un agent INLINE (via fsm).
"""

from typing import Any, Dict

from services.api.router import register


def _load_inline(params: dict) -> Dict[str, Any]:
    """Retourne l'agent INLINE (dict) depuis params :
      - data           : dict déjà inline (utilisé tel quel si self-contained)
      - yaml           : str yaml_simple → inline via compile_yaml
      - name [kind]    : chargé du catalogue → inline
    """
    from modules.agent_graph_utils import compile_yaml as cy

    data = params.get("data")
    if data is not None:
        # data peut déjà être un agent brut (yaml_simple) → inline pour garantir
        # le self-contained, sauf si déjà marqué inline.
        if data.get("inline"):
            return data
        return cy.inline_document(data, kind=params.get("kind"))

    yaml_str = params.get("yaml")
    if yaml_str:
        import yaml
        parsed = yaml.safe_load(yaml_str)
        if isinstance(parsed, dict):
            return cy.inline_document(parsed, kind=params.get("kind"))

    name = params.get("name")
    if name:
        res = cy.resolve_reference(name, kind=params.get("kind"))
        if res:
            return res
    return {}


def op_compile_yaml_inline(params: dict) -> Dict[str, Any]:
    """utils/compile/yaml-inline — inline un yaml_simple en self-contained.

    params : { data | yaml | name } + optionnel { kind: skill|agent|team }.
    Retourne {kind, name, inline: {…}}.
    """
    from modules.agent_graph_utils import compile_yaml as cy

    inline = _load_inline(params)
    if not inline:
        return {"status": "error", "error": "document introuvable (data/yaml/name requis)"}
    try:
        kind = inline.get("kind", cy.detect_kind(inline))
        return {"status": "ok", "kind": kind,
                "name": inline.get("name", ""),
                "inline": inline}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_graph_fsm(params: dict) -> Dict[str, Any]:
    """utils/graph/fsm — graphe FSM hiérarchique d'un agent INLINE.

    params : { data | yaml | name } — l'agent est INLINÉ avant construction.
    Retourne {nodes, edges, title} — logique pure, sans thème ni positions.
    """
    from modules.agent_graph_utils.fsm import yaml_to_fsm

    inline = _load_inline(params)
    if not inline:
        return {"status": "error", "error": "agent introuvable (data/yaml/name requis)"}
    try:
        g = yaml_to_fsm(inline)
        g["status"] = "ok"
        return g
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_graph_taskflow(params: dict) -> Dict[str, Any]:
    """utils/graph/taskflow — Pétri PLAT d'un agent INLINE (via fsm).

    params : { data | yaml | name } + { action, node } pour fold/unfold.
    """
    from modules.agent_graph_utils.taskflow import yaml_to_taskflow
    from modules.agent_graph_utils import petri as P

    inline = _load_inline(params)
    if not inline:
        return {"status": "error", "error": "agent introuvable (data/yaml/name requis)"}
    try:
        g = yaml_to_taskflow(inline)
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


register("utils/compile/yaml-inline", op_compile_yaml_inline)
register("utils/graph/fsm", op_graph_fsm)
register("utils/graph/taskflow", op_graph_taskflow)
