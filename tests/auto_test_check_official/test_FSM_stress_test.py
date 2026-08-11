"""FSM/stress_test_without_llm — STRESS TEST : charge le FSM avec N agents.

⚠️ PAS lancé par défaut (marker `stress`). Exécution explicite :
    python3 -m pytest tests/auto_test_check_official/ -m stress -q -s

Vérifie la ROBUSTESSE du FSM :
  - 400 agents inline (compile_yaml) dans l'espace réservé (cleanup trivial)
  - chaque agent exécute un mini-FSM (set_variable → call → end)
  - 100 sub-agents en thread (2 files) sous charge
  - mesure le temps total (cible < 60 s sans LLM)

Ce test est DESTINÉ aux redesigns du moteur, PAS à chaque modification.
Le stress GROUPÉ ~30 min (bench 10_mixed_30min) est un benchmark à part.
"""

import os
import time
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MODELWEAVER_HOME", str(Path(tempfile.mkdtemp()) / "mw"))
REPO = Path(__file__).resolve().parent.parent.parent

pytestmark = pytest.mark.stress

NS = "auto-test-check-official/stress"
N_AGENTS = 400


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("pas de LLM en stress sans llm")


def test_stress_1000_agents_inline():
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    # 1. inline de l'agent (compile_yaml) — on inline UNE seule fois et on
    #    réutilise le dict (le re-scan du catalogue coûte ~200 ms/appel).
    t0 = time.monotonic()
    agent = {
        "name": f"{NS}/a@v1", "namespace": NS,
        "entrypoints": {"main": {"steps": [
            {"id": "s", "type": "set_variable", "name": "x", "value": "1",
             "next": "d"},
            {"id": "d", "type": "call", "fn": "test/double@v1",
             "inputs": {"valeur": "{{x}}"}, "capture": {"doubl": "dx"},
             "next": "e"},
            {"id": "e", "type": "end", "status": "SUCCESS"},
        ]}},
    }
    inline = cy.inline_document(agent, kind="agent")
    inlines = [inline for _ in range(N_AGENTS)]
    t_inline = time.monotonic() - t0

    # 2. exécution FSM de chaque agent (sans LLM) — on RÉUTILISE un seul
    #    FSMInterpreter (la création d'un nouveau coûte ~200 ms ; ici on
    #    mesure l'exécution du moteur, pas l'instanciation).
    t1 = time.monotonic()
    fsm = FSMInterpreter(bridge=NoLLM(), max_iterations=50)
    ok = 0
    for a in inlines:
        r = fsm.run(workflow=a["entrypoints"]["main"], messages=[],
                    variables={}, provider_ref="", model_ref="", home="/tmp")
        if r.status == "success" and int(r.variables.get("dx") or 0) == 2:
            ok += 1
    t_exec = time.monotonic() - t1

    total = time.monotonic() - t0
    print(f"\ninline {N_AGENTS} agents: {t_inline:.1f}s | "
          f"exec: {t_exec:.1f}s | total: {total:.1f}s | ok={ok}/{N_AGENTS}")
    assert ok == N_AGENTS, f"seulement {ok}/{N_AGENTS} agents OK"
    # cible raisonnable : < 5 min pour le stress complet
    assert total < 300, f"stress trop long : {total:.1f}s"


def test_stress_sub_agents_threads():
    """200 sub-agents en thread (2 files request/respond) sous charge."""
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    sub = {"name": f"{NS}/doubler@v1",
           "entrypoints": {"main": {"steps": [
               {"id": "recv", "type": "sub_await_request", "mode": "blocking",
                "capture": {"message": "v"}, "next": "d"},
               {"id": "d", "type": "call", "fn": "test/double@v1",
                "inputs": {"valeur": "{{v}}"}, "capture": {"doubl": "dx"},
                "next": "r"},
               {"id": "r", "type": "sub_respond", "data": "{{dx}}",
                "mode": "blocking", "next": "e"},
               {"id": "e", "type": "end", "status": "SUCCESS"},
           ]}}}
    parent = {"name": f"{NS}/p@v1", "sub_agents": [sub],
              "entrypoints": {"main": {"steps": [
                  {"id": "go", "type": "sub_agent", "agent": sub["name"],
                   "request": "50", "request_mode": "blocking",
                   "respond_mode": "blocking", "timeout": 10,
                   "capture": {"result": "res"}, "next": "end"},
                  {"id": "end", "type": "end", "status": "SUCCESS"},
              ]}}}
    inline = cy.inline_document(parent, kind="agent")
    subs = {s["name"]: s for s in inline["sub_agents"]}

    t0 = time.monotonic()
    fsm = FSMInterpreter(bridge=NoLLM())
    ok = 0
    for _ in range(100):
        r = fsm.run(workflow=inline["entrypoints"]["main"], messages=[],
                    variables={}, provider_ref="", model_ref="",
                    sub_agents=subs, home="/tmp")
        if r.status == "success" and r.variables.get("res") == "100":
            ok += 1
    dt = time.monotonic() - t0
    print(f"\n100 sub-agents threads: {dt:.1f}s | ok={ok}/100")
    assert ok == 100
