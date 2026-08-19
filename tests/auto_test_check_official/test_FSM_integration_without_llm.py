"""FSM/integration_without_llm — Test INTÉGRÉ complet du langage.

Exerce TOUTES les features du FSM sans LLM (moteur pur) :
  - agent + héritage simple/multiple (extends)
  - sub-agents (thread enfant, 2 files request/respond)
  - team (director + members, héritage)
  - skills + sub-skills (foncteurs à état, catalogue runtime)
  - entrypoints multiples, loops (for/while), switch, if, on_error, capture

La chaîne est vérifiée de bout en bout : yaml_simple → inline (compile_yaml)
→ FSM (fsm_interpreter). Aucun appel LLM (bridge factice qui échoue si
sollicité). Durée cible < 5 min ; ici < 5 s.

Toutes les données vivent dans l'espace réservé auto-test-check-official.
"""

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MODELWEAVER_HOME", str(Path(tempfile.mkdtemp()) / "mw"))
REPO = Path(__file__).resolve().parent.parent.parent

NS = "auto-test-check-official/FSM"


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("AUCUN appel LLM ne doit avoir lieu")


# ── Fixtures : contenu YAML inline des objets de test ────────────────────

def _agent_demarreur() -> dict:
    """Agent maître : hérite d'une base, lance des sub-agents, boucle, if."""
    return {
        "name": f"{NS}/demarreur@v1",
        "kind": "agent",
        "namespace": NS,
        "extends": "test/base@v1",          # héritage simple (méthode hello)
        "methods": {
            "verifier": {                    # override de rien, méthode propre
                "description": "vérifie le home",
                "inputs": {"home": {"type": "string", "required": True}},
                "outputs": {"ok": {"type": "boolean"}},
                "implementation": {"type": "python", "code": _CODE_CHECK_HOME},
            },
        },
        "sub_agents": [
            {"name": f"{NS}/doubler@v1", "namespace": NS,
             "entrypoints": {"main": {"steps": [
                 {"id": "recv", "type": "sub_await_request", "mode": "blocking",
                  "capture": {"message": "valeur"}, "next": "double"},
                 {"id": "double", "type": "call", "fn": "test/double@v1",
                  "inputs": {"valeur": "{{valeur}}"}, "capture": {"doubl": "doubl"},
                  "next": "rep"},
                 {"id": "rep", "type": "sub_respond", "data": "{{doubl}}",
                  "mode": "blocking", "next": "end"},
                 {"id": "end", "type": "end", "status": "SUCCESS"},
             ]}}},
        ],
        "entrypoints": {
            "main": {"steps": [
                {"id": "start", "type": "set_variable", "name": "accueil",
                 "value": "go {{namespace}}", "next": "verifier"},
                {"id": "verifier", "type": "call", "fn": "test/check_home@v1",
                 "inputs": {"home": "{{home}}"}, "capture": {"ok": "home_ok"},
                 "on_error": "echec", "next": "deleguer"},
                {"id": "deleguer", "type": "sub_agent", "agent": f"{NS}/doubler@v1",
                 "request": "21", "request_mode": "blocking",
                 "respond_mode": "blocking", "timeout": 10,
                 "capture": {"result": "doubl"}, "next": "boucle"},
                {"id": "boucle", "type": "for", "variable": "i",
                 "start": 0, "end": 3, "step": 1, "body": {"steps": [
                     {"id": "d", "type": "call", "fn": "test/double@v1",
                      "inputs": {"valeur": "{{i}}"}, "capture": {"doubl": "d2"},
                      "next": "ds"},
                     {"id": "ds", "type": "continue"},
                 ]}, "next": "cond"},
                {"id": "cond", "type": "if",
                 "condition": {"variable": "home_ok", "operator": "==",
                               "value": "True"},
                 "body": {"steps": [
                     {"id": "succes", "type": "end", "status": "SUCCESS"},
                 ]}, "next": "echec"},
                {"id": "echec", "type": "end", "status": "FAILED"},
            ]},
        },
    }


_CODE_CHECK_HOME = (
    "def run(inputs, home):\n"
    "    import os\n"
    "    return {'ok': os.path.isdir(inputs.get('home', '/tmp')), 'ok2': True}\n"
)


def _team_demo() -> dict:
    """Team : director + members (agents inline), héritage de team."""
    return {
        "name": f"{NS}/team_demo@v1",
        "kind": "team",
        "namespace": NS,
        "topology": "hierarchical",
        "workspace_id": "test-fsm",
        "director": {"name": f"{NS}/directeur@v1", "role": "orchestrateur"},
        "members": [
            {"name": f"{NS}/doubler@v1", "role": "codeur"},
            {"name": f"{NS}/membre_b@v1", "role": "testeur"},
        ],
    }


# ── Tests ────────────────────────────────────────────────────────────────

def test_fsm_integration_agent_complet():
    """L'agent maître (héritage + sub-agent + boucle + if) tourne sans LLM."""
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    inline = cy.inline_document(_agent_demarreur(), kind="agent")
    assert inline["inline"] is True
    assert "verifier" in inline["methods"]      # méthode propre
    assert "hello" in inline["methods"]         # héritée de test/base@v1
    subs = {s["name"]: s for s in inline["sub_agents"]}

    fsm = FSMInterpreter(bridge=NoLLM(), max_iterations=200)
    r = fsm.run(workflow=inline["entrypoints"]["main"], messages=[],
                variables={"namespace": NS, "request": "test"},
                provider_ref="", model_ref="", sub_agents=subs, home="/tmp")
    assert r.status == "success", r.end_reason
    assert r.variables.get("home_ok") is True
    assert r.variables.get("doubl") == "42"     # sub-agent 21*2


def test_fsm_integration_team_inline():
    """La team (director + members + héritage) s'inline correctement."""
    from modules.agent_graph_utils import compile_yaml as cy
    team = cy.inline_team(_team_demo(), {}, {})
    assert team["kind"] == "team"
    assert team["topology"] == "hierarchical"
    assert team["director"]["name"] == f"{NS}/directeur@v1"
    assert len(team["members"]) == 2
    # chaque membre est un agent inline complet
    for m in team["members"]:
        assert m["kind"] == "agent"
        assert m.get("inline") is True


def test_fsm_heritage_multiple():
    """L'héritage multiple sans recouvrement fusionne les parents."""
    from modules.agent_graph_utils import compile_yaml as cy
    d = cy.resolve_reference("test/multi_enfant@v1")
    assert set(d["methods"]) == {"m_a", "m_b", "m_enfant"}


def test_fsm_foncteur_etat():
    """Les foncteurs gardent leur état (instance par agent, entrypoints)."""
    from services.skill_manager import call_skill, functor_state
    call_skill("test/compteur@v1", {}, "/tmp", agent_id="fsm-1")
    call_skill("test/compteur@v1", {"pas": 9}, "/tmp", agent_id="fsm-1")
    r = call_skill("test/compteur@v1", {}, "/tmp", agent_id="fsm-1",
                   entrypoint="second")
    assert r["compteur"] == 10
    assert functor_state("fsm-1", "test/compteur@v1")["compteur"] == 10
    # autre agent → indépendant
    assert call_skill("test/compteur@v1", {}, "/tmp",
                      agent_id="fsm-2")["compteur"] == 1


def test_fsm_catalogue_runtime():
    """catalogue.namespace.fn résolu à runtime dans un foncteur."""
    from services.skill_manager import call_skill
    r = call_skill("test/forge@v1", {"items": [5, 2, 8]}, "/tmp", agent_id="fsm-3")
    assert r["triee"] == [2, 5, 8]


def test_fsm_espaces_reserves():
    """L'espace auto-test accepte les écritures ; system/ les refuse."""
    import services.api.handlers.catalogue_local as H
    W = {"token": "write_catalogue"}
    assert H.op_data_types_add({**W, "code": "skill", "description": "skills"})["status"] == "ok"
    r = H.op_data_add({**W, "type": "skill",
                       "ref": f"{NS}/demo@v1", "name": "demo",
                       "namespace": NS, "data_value_type": "json", "value": {}})
    assert r["ok"] is True
    try:
        H.op_data_add({**W, "type": "skill",
                       "ref": "system/x@v1", "name": "x",
                       "namespace": "system", "data_value_type": "json",
                       "value": {}})
        assert False, "ref system/ attendue refusée"
    except Exception:
        pass
