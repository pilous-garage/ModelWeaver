"""Tests de l'héritage : multiple sans recouvrement (agent), héritage team,
héritage sub-agent, et les foncteurs (contrat + état + catalogue runtime)."""

from pathlib import Path

import pytest

from modules.agent_graph_utils import compile_yaml as cy

REPO = Path(__file__).resolve().parent.parent


def _ref(name):
    return cy.resolve_reference(name)


# ── Héritage multiple sans recouvrement ──────────────────────────────────

def test_heritage_multiple_fusionne():
    d = _ref("test/multi_enfant@v1")
    assert d.get("inline") is True
    methods = set(d["methods"])
    # m_a (parent A) + m_b (parent B) + m_enfant (sien)
    assert methods == {"m_a", "m_b", "m_enfant"}, methods
    assert "implementation" in d["methods"]["m_a"]
    assert "implementation" in d["methods"]["m_b"]


def test_heritage_conflit_leve():
    """Si 2 parents définissent la même méthode ET que l'enfant ne la
    redéfinit pas → conflit (erreur)."""
    a = {"name": "P1", "methods": {"x": {"description": "a"}},
         "entrypoints": {"main": {"steps": []}}}
    b = {"name": "P2", "methods": {"x": {"description": "b"}},
         "entrypoints": {"main": {"steps": []}}}
    enfant = {"name": "E", "extends": ["P1", "P2"],
              "entrypoints": {"main": {"steps": []}}}
    with pytest.raises(ValueError, match="conflit"):
        cy.inline_agent(enfant, {}, {"P1": a, "P2": b})


def test_heritage_conflit_override_ok():
    """Redéfinir le champ ambigu dans l'enfant lève le conflit (override)."""
    a = {"name": "P1", "methods": {"x": {"description": "a"}},
         "entrypoints": {"main": {"steps": []}}}
    b = {"name": "P2", "methods": {"x": {"description": "b"}},
         "entrypoints": {"main": {"steps": []}}}
    enfant = {"name": "E", "extends": ["P1", "P2"],
              "methods": {"x": {"description": "enfant"}},
              "entrypoints": {"main": {"steps": []}}}
    d = cy.inline_agent(enfant, {}, {"P1": a, "P2": b})
    assert d["methods"]["x"]["description"] == "enfant"


# ── Héritage team ────────────────────────────────────────────────────────

def test_inline_team_format_director():
    """Le format team réel utilise director (pas leader) + members."""
    team = {"name": "T", "topology": "flat", "workspace_id": "w",
            "director": {"name": "chef", "role": "orchestrateur"},
            "members": [{"name": "m1", "role": "codeur"}]}
    d = cy.inline_team(team, {}, {})
    assert d["kind"] == "team"
    assert d["topology"] == "flat"
    assert d["director"]["name"] == "chef"
    assert d["members"][0]["name"] == "m1"


def test_team_heritage_members_fusion():
    """Une team étend une autre : director/members hérités fusionnés."""
    parent = {"name": "TP", "topology": "hierarchical",
              "director": {"name": "chef"},
              "members": [{"name": "m_herite"}]}
    child = {"name": "TC", "extends": "TP",
             "members": [{"name": "m_propre"}]}
    d = cy.inline_team(child, {}, {}, {"TP": parent})
    names = [m["name"] for m in d["members"]]
    assert "m_herite" in names and "m_propre" in names, names
    assert d["director"]["name"] == "chef"
    assert d["topology"] == "hierarchical"


# ── Héritage sub-agent ───────────────────────────────────────────────────

def test_sub_agent_heritage():
    """Un sub-agent peut lui-même étendre un agent du catalogue."""
    parent = {"name": "base", "methods": {"m": {"description": "hérité"}},
              "entrypoints": {"main": {"steps": []}}}
    agent = {"name": "A", "sub_agents": [
        {"name": "sub", "extends": "base",
         "methods": {"m2": {"description": "propre"}},
         "entrypoints": {"main": {"steps": []}}}]}
    d = cy.inline_agent(agent, {}, {"base": parent})
    sub = d["sub_agents"][0]
    assert set(sub["methods"]) == {"m", "m2"}, sub["methods"]


# ── Foncteur : contrat + état + catalogue runtime ────────────────────────

def test_foncteur_etat_par_agent():
    from services.skill_manager import call_skill, functor_state
    r1 = call_skill("test/compteur@v1", {}, "/tmp", agent_id="7")
    call_skill("test/compteur@v1", {"pas": 5}, "/tmp", agent_id="7")
    r3 = call_skill("test/compteur@v1", {}, "/tmp", agent_id="7")
    assert r1["compteur"] == 1 and r3["compteur"] == 7, (r1, r3)
    # second entrypoint lit l'état
    r2 = call_skill("test/compteur@v1", {}, "/tmp", agent_id="7",
                    entrypoint="second")
    assert r2["compteur"] == 7
    # autre agent → instance indépendante
    r_autre = call_skill("test/compteur@v1", {}, "/tmp", agent_id="99")
    assert r_autre["compteur"] == 1
    assert functor_state("7", "test/compteur@v1")["compteur"] == 7


def test_foncteur_variable_objet_catalogue():
    """self.sort = catalogue.utils.bubble_sort → variable-objet (foncteur)."""
    from services.skill_manager import call_skill
    r = call_skill("test/forge@v1", {"items": [3, 1, 2]}, "/tmp", agent_id="7")
    assert r["triee"] == [1, 2, 3], r
    r2 = call_skill("test/forge@v1", {"items": [9, 4, 1]}, "/tmp", agent_id="7")
    assert r2["triee"] == [1, 4, 9], r2


def test_foncteur_restore_etat():
    from services.skill_manager import functor_restore_state, call_skill
    functor_restore_state("777", "test/compteur@v1", {"compteur": 100})
    r = call_skill("test/compteur@v1", {}, "/tmp", agent_id="777")
    assert r["compteur"] == 101, r
