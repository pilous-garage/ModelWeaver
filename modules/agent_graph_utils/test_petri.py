"""Tests du module agent_graph_utils.petri (folding zip/unzip)."""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from modules.agent_graph_utils import petri  # noqa: E402
from modules.agent_graph_utils.petri import (  # noqa: E402
    PetriNet, rebuild_groups, recheck, fold_seq, fold_par, fold_single, find_foldable,
)


def _chain():
    net = PetriNet()
    A = net.add_node("A", "place")
    t1 = net.add_node("t1", "transition")
    P = net.add_node("P", "place")
    t2 = net.add_node("t2", "transition")
    B = net.add_node("B", "place")
    for f, t in [("A", "t1"), ("t1", "P"), ("P", "t2"), ("t2", "B")]:
        net.add_edge(net.num_of(f), net.num_of(t))
    rebuild_groups(net)
    return net


def test_fold_place_generates_transition_and_counts():
    net = _chain()
    before = net.count_visible()
    P = net.num_of("P")
    recheck(net, P)
    assert net.node(P).foldable == "seq"
    z = fold_seq(net, P)
    after = net.count_visible()
    # seq sur une place → place -1, transition -1, arête -2 ; z est une TRANSITION
    assert z.class_ == "transition"
    assert after["places"] == before["places"] - 1
    assert after["transitions"] == before["transitions"] - 1
    assert after["edges"] == before["edges"] - 2
    # A → z → B (t1, P, t2 masqués)
    assert net.node(P).visible is False
    assert net.node(z.num_id).out_group == [net.num_of("B")]


def test_fold_transition_generates_place():
    net = _chain()
    t1 = net.num_of("t1")
    recheck(net, t1)
    assert net.node(t1).foldable == "seq"
    z = fold_seq(net, t1)
    # fold d'une transition → z PLACE
    assert z.class_ == "place"


def test_par_folds_transitions_same_inout():
    net = PetriNet()
    A = net.add_node("A", "place")
    B = net.add_node("B", "place")
    ts = [net.add_node(f"t{i}", "transition") for i in range(3)]
    for t in ts:
        net.add_edge(A.num_id, t.num_id)
        net.add_edge(t.num_id, B.num_id)
    rebuild_groups(net)
    for t in ts:
        recheck(net, t.num_id)
        assert net.node(t.num_id).foldable == "par"
    before = net.count_visible()
    z = fold_par(net, [t.num_id for t in ts])
    after = net.count_visible()
    # N transitions → 1 : transition -(N-1), arête -2*(N-1), place 0
    assert z.class_ == "transition"
    assert after["transitions"] == before["transitions"] - 2
    assert after["edges"] == before["edges"] - 4
    assert after["places"] == before["places"]


def test_single_box_one_place():
    net = PetriNet()
    box = net.add_node("box", "box")
    inner = net.add_node("box/x", "place", parent=box.num_id)
    out = net.add_node("out", "place")
    net.add_edge(inner.num_id, out.num_id)
    rebuild_groups(net)
    recheck(net, box.num_id)
    assert net.node(box.num_id).foldable == "single"
    z = fold_single(net, box.num_id)
    assert z.class_ == "transition"
    assert net.node(inner.num_id).visible is False


def test_boxes_transparent_in_groups():
    net = PetriNet()
    A = net.add_node("A", "place")
    box = net.add_node("box", "box")
    B = net.add_node("B", "place")
    net.add_edge(A.num_id, box.num_id)
    net.add_edge(box.num_id, B.num_id)
    rebuild_groups(net)
    # la box n'apparaît dans AUCUN groupe
    assert net.node(A.num_id).out_group == []
    assert net.node(B.num_id).in_group == []
    assert net.node(box.num_id).in_group == []
    assert net.node(box.num_id).out_group == []
