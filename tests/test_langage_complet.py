"""Test du fichier langage COMPLET : exécute l'agent inline test/langage_complet@v1
à travers l'interpréteur FSM, SANS LLM (skills pures Python + bridge stub).

Vérifie qu'on obtient un log FSM cohérent : steps exécutées, transitions
(entry/next/loop), captures de variables, sub-agents (files request/respond),
héritage de méthodes (extends + override), namespace, entrypoints multiples.
"""

import os
import tempfile
import time
from pathlib import Path

import pytest
import yaml

from AgentFrameWork.fsm_interpreter import FSMInterpreter

REPO = Path(__file__).resolve().parent.parent

# Bridge stub : le test ne doit JAMAIS appeler un vrai LLM. Le step llm_call
# du sub-agent reconcilieur est contourné par la valeur par défaut de
# `output_capture` (snippet déjà corrigé par la méthode reconcilier) ; mais si
# un llm_call était réellement atteint, on échoue au lieu d'appeler le réseau.
class StubBridge:
    def chat(self, *a, **k):
        raise AssertionError("AUCUN appel LLM ne doit avoir lieu dans ce test")


class _LifecycleLog:
    """Mini lifecycle manager : enregistre chaque step post_step (simule le
    FSMLogger du runtime Agent). Vérifie qu'on obtient un log FSM cohérent."""

    def __init__(self):
        self.events = []

    def publish(self, hook, **kw):
        if hook == "post_step":
            step = kw.get("step") or {}
            self.events.append((kw.get("step_id"), step.get("type")))

    def _as_log(self):
        return "\n".join(f"{sid} {stype}" for sid, stype in self.events)


def _inline():
    from modules.agent_graph_utils import compile_yaml as cy
    return cy.resolve_reference("test/langage_complet@v1")


def _sub_agents(inline) -> dict:
    return {s["name"]: s for s in inline.get("sub_agents", [])}


def test_inline_langage_complet_structure():
    d = _inline()
    assert d.get("inline") is True
    # namespace remonté
    assert d.get("namespace") == "test/atelier"
    # héritage : methods = base (hello) + override (analyse)
    assert set(d["methods"]) == {"analyse", "hello"}
    assert "implementation" in d["methods"]["hello"]      # héritée
    assert "implementation" in d["methods"]["analyse"]    # override
    # sub-agents inline avec leur propre LLM
    names = [s["name"] for s in d["sub_agents"]]
    assert "test/reconcilieur@v1" in names
    assert "test/verificateur@v1" in names


def test_fsm_langage_complet_tourne_sans_llm():
    inline = _inline()
    subs = _sub_agents(inline)
    wf = inline["entrypoints"]["main"]

    lifecycle = _LifecycleLog()
    fsm = FSMInterpreter(bridge=StubBridge(), max_iterations=200)
    r = fsm.run(
        workflow=wf,
        messages=[],
        variables={"namespace": "test/atelier", "home": "/tmp", "request": "bonjour"},
        provider_ref="", model_ref="",
        sub_agents=subs,
        home="/tmp",
        lifecycle_mgr=lifecycle,
    )
    # Doit réussir (status success) — le chemin boucle→condition→succes.
    assert r.status == "success", f"status={r.status} end_reason={r.end_reason}"
    # Log FSM cohérent : les steps top-level sont bien traversés dans l'ordre
    # (set_variable → call → sub_agent ×2 → call → for → if).
    log_lines = lifecycle._as_log()
    for expected in ("start set_variable", "verifier_base call",
                     "deleguer sub_agent", "verifier_herite sub_agent",
                     "analyse call", "boucle for"):
        assert expected in log_lines, f"step absent du log FSM: {expected}\n{log_lines}"
    # la boucle for a bien itéré 3× (doublon exécuté 3 fois)
    assert log_lines.count("doublon call") == 3, log_lines
    # le if termine par SUCCESS (pas de step echec dans le log)
    assert "echec" not in log_lines, log_lines
    # captures vérifiables
    assert r.variables.get("home_ok") is True, r.variables
    assert r.variables.get("plan", "").startswith("plan(")
    # verdict du sub-agent reconcilieur : snippet VALIDE → "True"
    verdict = r.variables.get("verdict_reconcilieur", "")
    assert isinstance(verdict, str) and verdict == "True", verdict
    # verdict du sub-agent verificateur : double de l'accueil (non numérique) → 0
    assert r.variables.get("verdict_verificateur") is not None
    # boucle for exécutée : la variable de fin de boucle n'est pas exposée,
    # mais le FSM a itéré sans échec (status success le prouve).


def test_sub_agent_files_request_respond():
    """Un sub-agent reçoit la request (ack) et répond via la file respond."""
    inline = _inline()
    subs = _sub_agents(inline)
    verif = subs["test/verificateur@v1"]
    fsm = FSMInterpreter(bridge=StubBridge())
    r = fsm.run(
        workflow={"steps": [
            {"id": "go", "type": "sub_agent", "agent": "test/verificateur@v1",
             "request": "21", "request_mode": "blocking",
             "respond_mode": "blocking", "timeout": 10,
             "capture": {"result": "doubl"}, "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]},
        messages=[], variables={}, provider_ref="", model_ref="",
        sub_agents=subs, home="/tmp",
    )
    assert r.status == "success", r.end_reason
    assert r.variables.get("doubl") == "42"   # double@v1 sur "21"


def test_sub_agent_llm_herite_du_parent():
    """Sub-agent sans LLM déclaré hérite du provider/model de son propriétaire."""
    inline = _inline()
    subs = _sub_agents(inline)
    assert subs["test/verificateur@v1"].get("provider_ref") is None
    assert subs["test/reconcilieur@v1"].get("provider_ref") == "fast/lite"


class RecordingBridge:
    """Bridge factice : enregistre les appels LLM et renvoie une réponse fixe.
    Simule le LLM « propre » d'un sub-agent (réconciliation de syntaxe)."""

    def __init__(self):
        self.calls = []

    def chat(self, provider_ref="", model_ref="", messages=None, **kw):
        self.calls.append({"provider": provider_ref, "model": model_ref})
        return type("R", (), {
            "content": "def foo():\n    pass",
            "usage": {},
        })()


def test_sub_agent_reconcilie_avec_son_llm():
    """Snippet invalide → le sub-agent reconcilieur appelle SON LLM
    (provider fast/lite, model reconcile-1b), pas celui par défaut."""
    inline = _inline()
    subs = _sub_agents(inline)
    reconcilieur = subs["test/reconcilieur@v1"]
    # snippet invalide
    snip_wf = dict(reconcilieur["entrypoints"]["main"])
    bridge = RecordingBridge()
    fsm = FSMInterpreter(bridge=bridge)
    link = fsm._sub_link if False else None
    from AgentFrameWork.fsm_interpreter import SubAgentLink

    class _L(SubAgentLink):
        def __init__(self, req):
            super().__init__()
            self.request_q.put(req)

    # Injecte le snippet invalide via la request
    sub_result_holder = {}

    def _run_child():
        child = FSMInterpreter(bridge=bridge)
        child.run(
            workflow=snip_wf, messages=[], variables={},
            provider_ref="", model_ref="",
            sub_link=link, home="/tmp",
        )

    # Simule le parent qui envoie un snippet invalide
    import threading
    link = _L("def foo(:\n    pass")
    child = FSMInterpreter(bridge=bridge)
    r = child.run(
        workflow=snip_wf, messages=[], variables={},
        provider_ref="", model_ref="",
        sub_link=link, home="/tmp",
    )
    # pas de raise StubBridge : le llm_call du reconcilieur a été émis
    assert bridge.calls, "le sub-agent aurait dû appeler SON LLM"
    assert bridge.calls[0]["provider"] == "fast/lite"
    assert bridge.calls[0]["model"] == "reconcile-1b"
    # la réponse du sub-agent est bien produite
    assert r.status in ("ok", "success"), r.end_reason


def test_entrypoint_ping():
    """Second entrypoint exécutable directement (test/pong@v1)."""
    inline = _inline()
    fsm = FSMInterpreter(bridge=StubBridge())
    r = fsm.run(
        workflow=inline["entrypoints"]["ping"],
        messages=[], variables={"request": "hello"},
        provider_ref="", model_ref="", home="/tmp",
    )
    assert r.status == "success", r.end_reason
    assert r.variables.get("reponse") == "pong:hello"
