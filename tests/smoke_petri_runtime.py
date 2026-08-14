"""SMOKE TEST — petri_runtime observe un run FSM simulé (sans LLM).

Objectif : valider le P1 runtime SANS run LLM réel.

  1. Prépare des BDD temporaires (agents + workspace) avec un agent hydraté
     et des sub_tasks ;
  2. Simule un run FSM (DummyBridge, steps factices) qui "fait avancer"
     l'agent à travers des steps → on reflète l'activité en agent_runtime
     (comme le fait le vrai agent_manager via post_step) ;
  3. On fait évoluer les sub_tasks (attributed → doing → done) pour simuler
     le travail ;
  4. petri_runtime.tick() observe → vérifie que :
     - l'agent est vu running avec le bon step ;
     - les pots externes (data) reflètent les sub_tasks ;
     - les assignments (qui fait quoi) sont corrects.

Usage :
    MODELWEAVER_HOME=$(mktemp -d) python3 tests/smoke_petri_runtime.py
"""

import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# BDD temporaires
_home = tempfile.mkdtemp(prefix="smoke_pr_")
os.environ["MODELWEAVER_HOME"] = _home


def setup_dbs():
    from services._common import mw_home
    home = mw_home()
    # agents.db : via AgentsDB (schéma complet) — pour resolve_entrypoint aussi
    from modules.sql.agents_repo import AgentsDB
    adb = AgentsDB()
    adb.conn.execute(
        "INSERT INTO agents (name, ref, role_type, status, state_json) "
        "VALUES (?,?,?,?,?)",
        ("greedy-coder", "agent:greedy-coder", "codeur", "RUNNING", "{}"))
    adb.conn.execute(
        "INSERT INTO agent_runtime (agent_id, thread_id, current_step, "
        "heartbeat_at) VALUES (1, 'tid-1', 'ask_new_task', 'x')")
    adb.conn.commit()
    adb.close()
    # workspace.db : sub_tasks
    wdb = sqlite3.connect(home / "workspace.db")
    wdb.execute("CREATE TABLE sub_tasks (sub_task_id INTEGER PRIMARY KEY, "
                "task_id INT, sub_task_type TEXT, status TEXT, assigned_to TEXT)")
    wdb.execute("INSERT INTO sub_tasks VALUES (1,10,'analysis','supervised','')")
    wdb.execute("INSERT INTO sub_tasks VALUES (2,10,'coding','doing','agent:1')")
    wdb.execute("INSERT INTO sub_tasks VALUES (3,10,'testing','unattributed','')")
    wdb.commit()
    wdb.close()
    return home


def simulate_fsm_step(step: str):
    """Reflète un step FSM dans agent_runtime (comme le vrai post_step)."""
    from services._common import mw_home
    adb = sqlite3.connect(f"file:{mw_home() / 'agents.db'}?mode=rwc", uri=True)
    adb.execute("UPDATE agent_runtime SET current_step = ? WHERE agent_id = 1",
                (step,))
    adb.commit()
    adb.close()


def simulate_subtask_advance():
    """Fait avancer coding doing → done, testing unattributed → doing."""
    from services._common import mw_home
    wdb = sqlite3.connect(f"file:{mw_home() / 'workspace.db'}?mode=rwc", uri=True)
    wdb.execute("UPDATE sub_tasks SET status = 'done' WHERE sub_task_id = 2")
    wdb.execute("UPDATE sub_tasks SET status = 'doing', assigned_to = 'agent:1' "
                "WHERE sub_task_id = 3")
    wdb.commit()
    wdb.close()


def main():
    setup_dbs()
    from services.petri_runtime import PetriRuntime
    pr = PetriRuntime()

    print("== Smoke petri_runtime ==")

    # 1) observation initiale
    snap = pr.tick()
    assert "greedy-coder" in snap["agents"], "agent non observé"
    a = snap["agents"]["greedy-coder"]
    assert a["state"] == "running", f"état attendu running, got {a['state']}"
    assert a["step"] == "ask_new_task", f"step initial {a['step']}"
    assert snap["data"]["coding"]["doing"] == 1
    print("  ✔ agent running + pots initiaux")

    # 2) simule l'avancée FSM : l'agent passe à do_work
    simulate_fsm_step("do_work")
    simulate_subtask_advance()
    snap = pr.tick()
    a = snap["agents"]["greedy-coder"]
    assert a["step"] == "do_work", f"step après avance {a['step']}"
    assert snap["data"]["coding"]["done"] == 1, "coding pas passé à done"
    assert snap["data"]["testing"]["doing"] == 1, "testing pas passé à doing"
    assert len(snap["assignments"]) == 1
    assert snap["assignments"][0]["type"] == "testing"
    print("  ✔ step FSM avancé + pots externes mis à jour")

    # 3) cohérence : types runtime ⊆ pétri statique
    from services.taskflow_petri import team_global_petri
    petri = team_global_petri(REPO / "services/manifests/teams/llm-code.team.yaml")
    types_static = set(petri.types)
    types_runtime = set(snap["data"].keys())
    assert types_runtime <= types_static, \
        f"types runtime {types_runtime} ⊄ statique {types_static}"
    print(f"  ✔ cohérence types runtime ⊆ pétri statique ({len(types_runtime)} types)")

    # 4) switch entrypoint : resolve_entrypoint avec signal ask_auth PENDING
    _test_entrypoint_switch()

    print()
    print(pr.render())
    print()
    print("SMOKE OK — petri_runtime observe le run FSM sans LLM")
    return 0


def _test_entrypoint_switch():
    """Valide resolve_entrypoint + le flux (stack/pop) sans run LLM."""
    from modules.sql.db import AgentsDB
    from services.agent_manager.service import Agent
    db = AgentsDB()
    config = {
        "entrypoints": {
            "main": {"steps": [{"id": "m", "type": "end"}]},
            "ask_auth": {"steps": [{"id": "auth", "type": "end"}]},
        }
    }
    cur = db.conn.execute(
        "INSERT INTO agents (name, ref, role_type, status, config_json) "
        "VALUES (?,?,?,?,?)",
        ("t-eps", "agent:t-eps", "codeur", "INIT", json.dumps(config)))
    aid = cur.lastrowid
    db.conn.commit()
    db.conn.executescript("""
        INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority)
            SELECT agent_id, 'main', 0 FROM agents;
        INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
            SELECT agent_id, 'cancel', 2, 'signal:cancel' FROM agents;
        INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
            SELECT agent_id, 'pause', 3, 'signal:pause' FROM agents;
        INSERT OR IGNORE INTO agent_entrypoints (agent_id, ep_name, priority, trigger)
            SELECT agent_id, 'ask_auth', 1, 'signal:ask_auth' FROM agents;
    """)
    db.conn.commit()
    row = db.conn.execute("SELECT * FROM agents WHERE agent_id = ?", (aid,)).fetchone()
    agent = Agent(db, dict(row))
    # sans signal → main
    assert agent.resolve_entrypoint() == "main", "défaut != main"
    # signal ask_auth → ask_auth
    db.conn.execute("INSERT INTO agent_signals (agent_id, type, payload_json) "
                    "VALUES (?, 'ask_auth', '{}')", (aid,))
    db.conn.commit()
    assert agent.resolve_entrypoint() == "ask_auth", "ask_auth non résolu"
    # signal pause (priorité 3) + ask_auth (1) → pause prime
    db.conn.execute("INSERT INTO agent_signals (agent_id, type, payload_json) "
                    "VALUES (?, 'pause', '{}')", (aid,))
    db.conn.commit()
    assert agent.resolve_entrypoint() == "pause", "pause ne prime pas"
    # flux dans le pétri : stack/pop présents pour l'agent
    from services.taskflow_petri import build_from_yaml
    r = build_from_yaml(REPO / "AgentsCatalogue/agents/greedy-coder.agent.yaml")
    net = r["petri"]
    stack = [t["name"] for t in net.transitions
             if t["name"].startswith("stack_")]
    pop = [t["name"] for t in net.transitions if t["name"].startswith("pop_")]
    assert stack and pop, "transitions stack/pop absentes"
    print(f"  ✔ resolve_entrypoint (main/ask_auth/pause) + flux stack/pop "
          f"({len(stack)} stack, {len(pop)} pop)")
    db.close()


if __name__ == "__main__":
    sys.exit(main())
