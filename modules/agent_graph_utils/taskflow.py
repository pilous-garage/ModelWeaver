"""taskflow.py — yaml_to_taskflow : transforme le FSM en Réseau de Pétri PLAT.

Représentation : PetriNet (nœuds num_id/class/specialty/parent, arêtes
from_/to). Les boxes sont des nœuds 'box' ; les enfants portent parent.

Transformation :
  - chaque nœud FSM LEAF → une PLACE (specialty : entrypoint/endpoint/tokenbucket)
  - chaque nœud FSM structurel (while/switch, vars.inner non vide) → une BOX
  - chaque ARÊTE FSM → une TRANSITION (from → t → to), routée à travers les
    boxes (l'entrée entre par l'entrypoint, la sortie sort par un exitpoint).

Le folding (zip/unzip, visibilité, fold-all) se fait côté backend sur ce
Pétri plat — le GUI n'envoie que l'action et reçoit le graphe (visible).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .fsm import yaml_to_fsm
from .petri import (
    PetriNet,
    CLASS_TRANSITION, CLASS_PLACE, CLASS_BOX,
    SPECIALTY_ENTRY, SPECIALTY_EXIT, SPECIALTY_TOKEN,
)


def _specialty(node: Dict[str, Any]) -> Optional[str]:
    tags = node.get("tags") or []
    t = node.get("type")
    if t in ("exitpoint", "exit_error") or "exitpoint" in tags:
        return SPECIALTY_EXIT
    if t == "entrypoint" or "entrypoint" in tags:
        return SPECIALTY_ENTRY
    if "tok_in" in tags or "tok_out" in tags:
        return SPECIALTY_TOKEN
    return None


def _resolve(net: PetriNet, fsm_by_id: Dict[str, Dict[str, Any]],
             fsm_num: Dict[str, int], node_id: str, is_exit: bool) -> int:
    """Résout un id FSM (place ou box) vers une PLACE feuille (entry/exit)."""
    cur_id = node_id
    for _ in range(12):
        num = fsm_num.get(cur_id)
        if num is None:
            return fsm_num.get(node_id, -1)
        n = net.node(num)
        if n.class_ != CLASS_BOX:
            return num
        node = fsm_by_id.get(cur_id)
        if not node:
            return num
        inner = node.get("vars", {}).get("inner") or {}
        nxt = ((inner.get("exitpoints") or [inner.get("entrypoint")])[0]
               if is_exit else inner.get("entrypoint"))
        if not nxt:
            return num
        cur_id = nxt
    return fsm_num.get(node_id, -1)


def yaml_to_taskflow(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Construit le Pétri plat d'un agent depuis son FSM.

    Retourne : {status, nodes: [...], edges: [...], title}
    (nodes = PetriNode sérialisés, edges = PetriEdge sérialisés).
    """
    fsm = yaml_to_fsm(data)
    net = PetriNet()
    fsm_num: Dict[str, int] = {}
    fsm_by_id: Dict[str, Dict[str, Any]] = {}
    all_edges: List[Dict[str, Any]] = []

    def collect(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            fsm_by_id[n["id"]] = n
            if n.get("vars", {}).get("inner", {}).get("nodes"):
                collect(n["vars"]["inner"]["nodes"])

    collect(fsm["nodes"])

    # 1) places + boxes
    def walk(nodes: List[Dict[str, Any]], parent: Optional[int]) -> None:
        for n in nodes:
            inner = n.get("vars", {}).get("inner") or {}
            if inner.get("nodes"):
                box = net.add_node(n["id"], CLASS_BOX, specialty=_specialty(n),
                                   type=n["type"], label=n.get("label", ""),
                                   parent=parent, vars=n.get("vars"))
                fsm_num[n["id"]] = box.num_id
                walk(inner["nodes"], box.num_id)
            else:
                p = net.add_node(n["id"], CLASS_PLACE, specialty=_specialty(n),
                                 type=n["type"], label=n.get("label", ""),
                                 parent=parent, vars=n.get("vars"))
                fsm_num[n["id"]] = p.num_id

    walk(fsm["nodes"], None)

    # 2) arêtes FSM (top + inner) → transitions
    def collect_edges(nodes: List[Dict[str, Any]]) -> None:
        for n in nodes:
            inner = n.get("vars", {}).get("inner") or {}
            for e in inner.get("edges") or []:
                all_edges.append(e)
            if inner.get("nodes"):
                collect_edges(inner["nodes"])

    all_edges.extend(fsm.get("edges") or [])
    collect_edges(fsm["nodes"])

    for e in all_edges:
        src = _resolve(net, fsm_by_id, fsm_num, e["from"], is_exit=True)
        dst = _resolve(net, fsm_by_id, fsm_num, e["to"], is_exit=False)
        if src < 0 or dst < 0:
            continue
        t = net.add_node(f"t:{e['from']}->{e['to']}", CLASS_TRANSITION,
                         type="next", label=e.get("label", ""),
                         parent=net.node(src).parent)
        net.add_edge(src, t.num_id, type=e.get("type", "next"), label=e.get("label", ""))
        net.add_edge(t.num_id, dst, type=e.get("type", "next"), label=e.get("label", ""))

    return {
        "status": "ok",
        "title": (data or {}).get("name", "agent"),
        "nodes": [{"num_id": n.num_id, "id": n.id, "class": n.class_,
                   "specialty": n.specialty, "type": n.type, "label": n.label,
                   "parent": n.parent, "visible": n.visible, "zipped": n.zipped,
                   "foldable": n.foldable, "vars": n.vars}
                  for n in net.nodes],
        "edges": [{"num_id": e.num_id, "from": e.from_, "to": e.to,
                   "type": e.type, "label": e.label, "visible": e.visible}
                  for e in net.edges],
    }
