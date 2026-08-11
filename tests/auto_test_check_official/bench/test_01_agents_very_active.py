"""BENCH 01 — agents TRÈS ACTIFS (sans sleep).

Mesure : jusqu'à COMBIEN d'agents tournent en boucle serrée (mini-FSM + skills
simples) sans LLM, et avec quelles ressources. Benchmark de la machine, pas un
test de correction.

Marker : bench. Exécution :
    python3 -m pytest tests/auto_test_check_official/bench/ -m bench -q -s

Rapport : {n_agents_max, rythme_agents_s, resources{...}}.
"""

import os
import time
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MODELWEAVER_HOME", str(Path(tempfile.mkdtemp()) / "mw"))

pytestmark = pytest.mark.bench

NS = "auto-test-check-official/bench/very_active"
PALIER = 25          # +25 agents par palier
PALIER_INTERVAL = 2  # secondes entre chaque palier
N_MAX = 150          # agents max du benchmark


class NoLLM:
    def chat(self, *a, **k):
        raise AssertionError("pas de LLM en bench")


def _agent_inline():
    """Mini-agent sans LLM : set_variable → call test/double → end."""
    from modules.agent_graph_utils import compile_yaml as cy
    return cy.inline_document({
        "name": f"{NS}/a@v1", "namespace": NS,
        "entrypoints": {"main": {"steps": [
            {"id": "s", "type": "set_variable", "name": "x", "value": "2",
             "next": "d"},
            {"id": "d", "type": "call", "fn": "test/double@v1",
             "inputs": {"valeur": "{{x}}"}, "capture": {"doubl": "dx"},
             "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]}},
    }, kind="agent")


def test_bench_very_active():
    from AgentFrameWork.fsm_interpreter import FSMInterpreter
    from tests.auto_test_check_official.bench.resources import ResourceMonitor

    inline = _agent_inline()
    # on pré-inline les N_MAX agents (la compilation n'est pas ce qu'on mesure)
    inlines = [inline for _ in range(N_MAX)]

    mon = ResourceMonitor(interval_s=1.0)
    mon.start()
    t0 = time.monotonic()
    n_ok = 0
    n_current = 0
    while n_current < N_MAX:
        # un palier : lancer PALIER agents en boucle (chaque agent fait son FSM)
        for _ in range(PALIER):
            fsm = FSMInterpreter(bridge=NoLLM())
            r = fsm.run(workflow=inlines[n_current]["entrypoints"]["main"],
                        messages=[], variables={}, home="/tmp")
            if r.status == "success":
                n_ok += 1
            n_current += 1
        # laisse le moniteur échantillonner entre les paliers
        time.sleep(PALIER_INTERVAL)

    dt = time.monotonic() - t0
    report = mon.stop()
    print(f"\n[bench very_active] agents={n_ok}/{N_MAX} en {dt:.0f}s "
          f"({n_ok/dt:.1f}/s) | cpu {report['cpu']} | "
          f"ram {report['ram_mb']} | fds {report['fds']}")
    # garde-fou grossier : le benchmark doit au moins passer le moteur
    assert n_ok >= 10, "le moteur a échoué sur >10 agents"
