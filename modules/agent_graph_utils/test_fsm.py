"""Tests du module agent_graph_utils.fsm (yaml_to_fsm + héritage de tags)."""
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from modules.agent_graph_utils.fsm import yaml_to_fsm, HERITABLE_TAGS  # noqa: E402

REPO = Path(__file__).resolve().parent.parent.parent


def _load(name):
    return yaml.safe_load((REPO / "AgentsCatalogue" / "agents" / name).read_text())


def test_yaml_to_fsm_structure():
    g = yaml_to_fsm(_load("greedy-coder.agent.yaml"))
    assert g["nodes"][0]["id"] == "main"
    assert g["nodes"][0]["type"] == "entrypoint"
    assert g["edges"][0]["from"] == "main"
    assert any(n["id"] == "working_loop" for n in g["nodes"])


def test_tag_inheritance_llm():
    g = yaml_to_fsm(_load("greedy-coder.agent.yaml"))
    wl = next(n for n in g["nodes"] if n["id"] == "working_loop")
    inner = wl["vars"]["inner"]
    # le body hérite du tag `llm` de working_loop
    for sub in inner["nodes"]:
        assert "llm" in sub["tags"]


def test_tok_in_tok_out_waiting():
    g = yaml_to_fsm(_load("greedy-coder.agent.yaml"))
    by = {n["id"]: n["tags"] for n in g["nodes"]}
    assert "tok_in" in by["pick"]          # token_task_pick → consomme
    assert "tok_out" in by["finalisation"] # end_exec → produit
    assert "tok_out" in by["release"]      # token_task_release → produit
    assert "waiting" in by["sleep"]        # wait_for


def test_heritable_categories():
    assert "llm" in HERITABLE_TAGS
    assert "sandbox" in HERITABLE_TAGS
    assert "waiting" in HERITABLE_TAGS
    assert "signal" in HERITABLE_TAGS
    assert "tok_in" in HERITABLE_TAGS
    assert "tok_out" in HERITABLE_TAGS
    assert "heavy_tools" in HERITABLE_TAGS


def test_route_registered():
    import services.api.handlers  # noqa: F401  (enregistre les routes)
    from services.api.router import dispatch
    r = dispatch("graphe_utile/yaml_to_fsm", {"name": "greedy-coder"})
    assert r.get("status") == "ok"
    assert len(r.get("nodes", [])) > 10
