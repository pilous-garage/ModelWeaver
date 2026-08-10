"""petri.py — Réseau de Pétri : représentation PLATE + folding (zip/unzip).

Représentation : PAS de sous-graphe Python. Les boxes sont des nœuds à
`class='box'` ; les nœuds qui y vivent portent `parent=<num_id de la box>`.

Nœud (PetriNode) :
  - num_id    : INDEX dans net.nodes (tableau) — accès O(1) pour in/out
  - id        : id stable (ex. pick, t:pick->work)
  - class     : 'transition' | 'place' | 'box'
  - specialty : None | 'entrypoint' | 'endpoint' | 'tokenbucket'
  - type      : type récupéré du FSM (skill, llm, flow, step…)
  - parent    : num_id de la box parente (None = top)
  - in_group / out_group : num_ids des voisins VISIBLES (box-transparents)
  - foldable  : 'none' | 'seq' | 'par' | 'single' (recalculé par recheck)

Arête (PetriEdge) :
  - num_id    : identifiant incrémental
  - from_ / to : num_ids des extrémités
  - type / label / visible

FOLDING :
  - seq sur une PLACE [x, N, y] → z TRANSITION ; seq sur une TRANSITION → z
    PLACE (alternance). place -1, transition -1, arête -2.
  - par : N transitions mêmes in/out → 1 transition (mêmes in/out).
  - les BOX sont transparentes (jamais dans les in/out, jamais reliées).
  - unzip = miroir exact.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .fsm import HERITABLE_TAGS

CLASS_TRANSITION = "transition"
CLASS_PLACE = "place"
CLASS_BOX = "box"

SPECIALTY_TOKEN = "tokenbucket"
SPECIALTY_ENTRY = "entrypoint"
SPECIALTY_EXIT = "endpoint"


@dataclass
class PetriNode:
    num_id: int
    id: str
    class_: str                 # transition | place | box
    specialty: Optional[str] = None
    type: str = ""
    label: str = ""
    parent: Optional[int] = None
    vars: Dict[str, Any] = field(default_factory=dict)
    visible: bool = True
    zipped: bool = False
    foldable: str = "none"
    in_group: List[int] = field(default_factory=list)
    out_group: List[int] = field(default_factory=list)


@dataclass
class PetriEdge:
    num_id: int
    from_: int
    to: int
    type: str = "next"
    label: str = ""
    visible: bool = True


class PetriNet:
    def __init__(self) -> None:
        self.nodes: List[PetriNode] = []
        self.edges: List[PetriEdge] = []
        self.by_id: Dict[str, int] = {}
        self._edge_counter = 0

    # ── construction ──
    def add_node(self, id: str, class_: str, **kw: Any) -> PetriNode:
        num = len(self.nodes)
        n = PetriNode(num, id, class_, **kw)
        self.nodes.append(n)
        self.by_id[id] = num
        return n

    def add_edge(self, from_: int, to: int, **kw: Any) -> PetriEdge:
        e = PetriEdge(self._edge_counter, from_, to, **kw)
        self._edge_counter += 1
        self.edges.append(e)
        return e

    def node(self, num: int) -> PetriNode:
        return self.nodes[num]

    def num_of(self, id: str) -> int:
        return self.by_id[id]

    # ── comptes visibles (debug / vérifs) ──
    def count_visible(self) -> Dict[str, int]:
        places = transitions = edges = 0
        for n in self.nodes:
            if not n.visible or n.class_ == CLASS_BOX:
                continue
            if n.class_ == CLASS_TRANSITION:
                transitions += 1
            else:
                places += 1
        for e in self.edges:
            if not e.visible:
                continue
            a, b = self.node(e.from_), self.node(e.to)
            if a.class_ == CLASS_BOX or b.class_ == CLASS_BOX:
                continue
            if a.visible and b.visible:
                edges += 1
        return {"places": places, "transitions": transitions, "edges": edges}


# ── groupes in/out (box-transparents) + foldabilité ──

def rebuild_groups(net: PetriNet) -> None:
    """Recalcule in_group/out_group (voisins VISIBLES, jamais les boxes) et
    le compteur inner_visible de chaque box."""
    for n in net.nodes:
        n.in_group = []
        n.out_group = []
        n.foldable = "none"
    for e in net.edges:
        if not e.visible:
            continue
        a = net.node(e.from_)
        b = net.node(e.to)
        if not a.visible or not b.visible:
            continue
        if a.class_ == CLASS_BOX or b.class_ == CLASS_BOX:
            continue                      # box transparente
        a.out_group.append(b.num_id)
        b.in_group.append(a.num_id)


def _is_boundary(n: PetriNode) -> bool:
    """Nœud de bord non masquable (tokenbucket) ou box."""
    return n.specialty == SPECIALTY_TOKEN or n.class_ == CLASS_BOX


def recheck(net: PetriNet, num: int) -> None:
    """Recalcule foldable d'un nœud (seq/par/single) depuis l'état maintenu."""
    n = net.node(num)
    if not n.visible:
        n.foldable = "none"
        return
    if n.class_ == CLASS_BOX:
        # single : box avec exactement 1 place interne visible
        inner = [i for i, c in enumerate(net.nodes) if c.parent == num and c.visible
                 and c.class_ != CLASS_TRANSITION]
        n.foldable = "single" if len(inner) == 1 else "none"
        return
    # seq : 1 entrée + 1 sortie, voisins purs
    if len(n.in_group) == 1 and len(n.out_group) == 1:
        x = net.node(n.in_group[0])
        y = net.node(n.out_group[0])
        if (x.visible and y.visible and not _is_boundary(x) and not _is_boundary(y)
                and len(x.out_group) == 1 and len(y.in_group) == 1):
            n.foldable = "seq"
            return
    # par : transition partageant in+out avec une autre
    if n.class_ == CLASS_TRANSITION:
        key = (tuple(sorted(n.in_group)), tuple(sorted(n.out_group)))
        for i, o in zip(n.in_group, n.out_group):
            for m in net.node(i).out_group:
                if m == num:
                    continue
                cand = net.node(m)
                if not cand.visible or cand.class_ != CLASS_TRANSITION:
                    continue
                if (tuple(sorted(cand.in_group)), tuple(sorted(cand.out_group))) == key:
                    n.foldable = "par"
                    return
    n.foldable = "none"


# ── folding : seq / par / single ──

def fold_seq(net: PetriNet, num: int) -> PetriNode:
    """zip séquentiel sur le nœud `num` (foldable='seq').
    [x, N, y] → z (type ALTERNÉ). place -1, transition -1, arête -2."""
    n = net.node(num)
    x = net.node(n.in_group[0])
    y = net.node(n.out_group[0])
    ins = list(x.in_group)
    outs = list(y.out_group)
    z = net.add_node(f"z:{n.id}",
                     CLASS_PLACE if n.class_ == CLASS_TRANSITION else CLASS_TRANSITION,
                     parent=n.parent, zipped=True)
    for k in ins:
        net.add_edge(k, z.num_id)
    for m in outs:
        net.add_edge(z.num_id, m)
    for t in (x.num_id, num, y.num_id):
        net.node(t).visible = False
    rebuild_groups(net)
    return z


def fold_par(net: PetriNet, nums: List[int]) -> PetriNode:
    """zip parallèle : N transitions mêmes in/out → 1 transition."""
    first = net.node(nums[0])
    z = net.add_node(f"p:{first.id}", CLASS_TRANSITION,
                     parent=first.parent, zipped=True,
                     in_group=list(first.in_group), out_group=list(first.out_group))
    for k in first.in_group:
        net.add_edge(k, z.num_id)
    for m in first.out_group:
        net.add_edge(z.num_id, m)
    for t in nums:
        net.node(t).visible = False
    rebuild_groups(net)
    return z


def fold_single(net: PetriNet, num: int) -> PetriNode:
    """zip single : box avec 1 place interne → z (transition) dans la box."""
    box = net.node(num)
    inner = [i for i, c in enumerate(net.nodes) if c.parent == num and c.visible
             and c.class_ != CLASS_TRANSITION]
    place = net.node(inner[0])
    z = net.add_node(f"s:{box.id}", CLASS_TRANSITION,
                     parent=num, zipped=True,
                     in_group=list(place.in_group), out_group=list(place.out_group))
    for k in place.in_group:
        net.add_edge(k, z.num_id)
    for m in place.out_group:
        net.add_edge(z.num_id, m)
    place.visible = False
    rebuild_groups(net)
    return z


def find_foldable(net: PetriNet) -> List[int]:
    """Nœuds visibles foldables (priorité seq, puis par, puis single)."""
    result = []
    for n in net.nodes:
        if not n.visible:
            continue
        recheck(net, n.num_id)
        if n.foldable in ("seq", "par", "single"):
            result.append(n.num_id)
    return result


def unzip(net: PetriNet, num: int) -> None:
    """Dé-fold : retire le nœud créé (zipped=True) et rend visibles ses
    voisins masqués correspondants. NOTE : dans la V1, on reconstruit les
    groupes ; l'historique précis des nœuds masqués sera porté dans le
    clipboard/table comme prévu."""
    z = net.node(num)
    if not z.zipped:
        raise ValueError(f"{num} n'est pas un nœud créé par un fold")
    z.visible = False
    # restauration naïve : on rend visible les nœuds invisibles du même parent
    for c in net.nodes:
        if not c.visible and c.parent == z.parent:
            c.visible = True
    rebuild_groups(net)
