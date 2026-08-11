"""test_minimal — SANITY rapide : vérifie que rien n'est cassé.

À lancer à CHAQUE modification (remplace la checkliste complète) :
    python3 -m pytest tests/test_minimal.py -q

Couvre, en < 10 s, un smoke des briques critiques :
  - inline compile_yaml (skill/agent/team + héritage)
  - FSM interpreter (un agent simple tourne sans LLM)
  - catalogue_local (lecture/écriture + réservation espace test)
  - catalogue_runtime (catalogue.namespace.fn)
  - foncteurs (état par agent)

Le STRESS_TEST complet (1000 agents, auth, teams…) est réservé à
tests/auto_test_check_official/ — pas à chaque modif.
"""

import os
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("MODELWEAVER_HOME", str(Path(tempfile.mkdtemp()) / "mw"))

REPO = Path(__file__).resolve().parent.parent


def test_compile_yaml_inline_ok():
    from modules.agent_graph_utils import compile_yaml as cy
    d = cy.resolve_reference("test/langage_complet@v1")
    assert d.get("inline") is True
    assert d.get("kind") == "agent"
    assert len(d.get("sub_agents", [])) == 2


def test_heritage_multiple_ok():
    from modules.agent_graph_utils import compile_yaml as cy
    d = cy.resolve_reference("test/multi_enfant@v1")
    assert set(d["methods"]) == {"m_a", "m_b", "m_enfant"}


def test_fsm_tourne_sans_llm():
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    class NoLLM:
        def chat(self, *a, **k):
            raise AssertionError("AUCUN appel LLM attendu")

    inline = cy.resolve_reference("test/langage_complet@v1")
    subs = {s["name"]: s for s in inline["sub_agents"]}
    fsm = FSMInterpreter(bridge=NoLLM(), max_iterations=200)
    r = fsm.run(workflow=inline["entrypoints"]["main"], messages=[],
                variables={"namespace": "test/atelier", "request": "bonjour"},
                provider_ref="", model_ref="", sub_agents=subs, home="/tmp")
    assert r.status == "success", r.end_reason


def test_catalogue_local_read_write():
    import services.api.handlers.catalogue_local as H
    W = {"token": "write_catalogue"}
    assert H.op_write_create_type({**W, "type": "skill"})["status"] in ("ok", "exists")
    r = H.op_write_upsert({**W, "type": "skill", "ref": "test/minimal@v1",
                           "name": "minimal", "namespace": "test", "value": {"x": 1}})
    assert r["status"] == "ok"
    e = H.op_get({"type": "skill", "ref": "test/minimal@v1"})["entry"]
    assert e["value"] == '{"x": 1}'


def test_catalogue_reserved_space():
    import services.api.handlers.catalogue_local as H
    W = {"token": "write_catalogue"}
    r = H.op_write_upsert({**W, "type": "skill",
                           "ref": "auto-test-check-official/smoke@v1",
                           "name": "smoke", "namespace": "auto-test-check-official",
                           "value": {}})
    assert r["status"] == "ok"
    r_bad = H.op_write_upsert({**W, "type": "skill",
                               "ref": "system/x@v1", "name": "x",
                               "namespace": "system", "value": {}})
    assert r_bad["status"] == "error"


def test_catalogue_runtime_foncteur():
    from services.skill_manager import call_skill
    r = call_skill("test/forge@v1", {"items": [3, 1, 2]}, "/tmp", agent_id="min1")
    assert r["triee"] == [1, 2, 3]
    c = call_skill("test/compteur@v1", {}, "/tmp", agent_id="min1")
    call_skill("test/compteur@v1", {"pas": 5}, "/tmp", agent_id="min1")
    r2 = call_skill("test/compteur@v1", {}, "/tmp", agent_id="min1")
    assert c["compteur"] == 1 and r2["compteur"] == 7


def test_routes_registerees():
    import services.api.handlers  # noqa: F401
    from services.api.router import dispatch
    assert dispatch("catalogue_local/list_catalogues", {}).get("status") in ("ok", "error")
    assert dispatch("catalogue_local/priv/check", {"chemin": "/x"}).get("status") in ("ok", "error")
    assert dispatch("catalogue_local/write/batch", {}).get("status") == "error"  # sans token


def test_resolution_path_typed():
    """Le PathEvaluator : chaînage typé + None.return sur récepteur vide."""
    from modules.agent_graph_utils.resolution import (
        PathEvaluator, make_root, SINGLETON, NONE_RETURN,
    )
    import services.catalogue_objects as CO  # noqa: F401 (enregistre les méthodes)

    members = [{"agent_id": 7, "name": "codeur-1", "role_type": "codeur"},
               {"agent_id": 8, "name": "codeur-2", "role_type": "codeur"}]
    team = make_root("team", {"members": members}, SINGLETON, {"agent_id": 1})
    ev = PathEvaluator(team)
    # chaîne complète
    r = ev.evaluate("team.members.reduce_pattern(name=codeur-*).first.get_home()")
    assert str(r).endswith("/agent_home/7"), r
    # récepteur vide → None.return (no-op, pas d'erreur)
    r2 = ev.evaluate("team.members.reduce_pattern(name=zzz*).first.get_home()")
    assert r2 is NONE_RETURN, r2
    # chaîne simple
    assert ev.evaluate("team.chatroom.read()") is not None


def test_resolution_team_chatroom_send():
    """team.chatroom.send(msg) : méthode directe, écrit réellement en BDD."""
    from modules.sql.workspace import WorkspaceDB
    from modules.agent_graph_utils.resolution import (
        PathEvaluator, make_root, SINGLETON,
    )
    import services.catalogue_objects as CO  # noqa: F401

    wdb = WorkspaceDB()
    try:
        wdb.workspaces.create("test-chat-res", name="Chat Res")
    except Exception:
        pass
    wdb.close()

    team = {"team_id": 5, "workspace_id": "test-chat-res",
            "members": [{"agent_id": 7, "name": "codeur-1",
                         "role_type": "codeur"}]}
    team_obj = make_root("team", team, SINGLETON, {"agent_id": 7})
    ev = PathEvaluator(team_obj)
    ev.evaluate("team.chatroom.send(salut-team)")

    wdb = WorkspaceDB()
    try:
        msgs = [m["content"]
                for m in wdb.for_workspace("test-chat-res").chat.recent(5)]
    finally:
        wdb.close()
    assert "salut-team" in msgs, msgs


def test_resolution_ambiguite_singleton():
    """singleton non déclaré → ambiguïté (erreur) si deux minima incomparables."""
    from modules.agent_graph_utils.resolution import (
        RuntimeObject, GLOBAL_REGISTRY, LIST_NOT_EMPTY, SINGLETON,
        SINGLETON_OR_NONE, ResolutionError,
    )
    GLOBAL_REGISTRY.register("obj_amb", "m", LIST_NOT_EMPTY, LIST_NOT_EMPTY,
                             lambda items, ctx=None, **kw: items)
    GLOBAL_REGISTRY.register("obj_amb", "m", SINGLETON_OR_NONE, SINGLETON,
                             lambda item, ctx=None, **kw: item)
    o = RuntimeObject(1, SINGLETON, "obj_amb", GLOBAL_REGISTRY, {})
    try:
        o.m()
        assert False, "ambiguïté non levée"
    except ResolutionError:
        pass


def test_resolution_dollar_dynamique():
    """$var dans fn/inputs : scope hiérarchique + mélange dynamique/littéral."""
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    wf = {"steps": [
        {"id": "s1", "type": "set_variable", "name": "lib", "value": "test",
         "next": "s2"},
        {"id": "s2", "type": "set_variable", "name": "base", "value": "21",
         "next": "call"},
        {"id": "call", "type": "call", "fn": "catalogue.$lib.check_home@v1",
         "inputs": {"home": "{{home}}"}, "capture": {"ok": "ok"}, "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    fsm = FSMInterpreter(bridge=None)
    r = fsm.run(workflow=wf, messages=[], variables={}, home="/tmp")
    assert r.status == "success", r.end_reason
    assert r.variables.get("ok") is True   # fn résolu → test/check_home@v1


def test_sub_skills_inline():
    """Sub-skill définie au plus haut niveau de l'inline, exécutée sans
    passer par le catalogue (d1=30), + skill catalogue (d2=10)."""
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    agent = {
        "name": "test/subskills@v1",
        "sub_skills": [
            {"name": "inline/triple@v1", "implementation": {
                "type": "python",
                "code": "def run(inputs, home):\n"
                        "    v=int(inputs.get('valeur',0))\n"
                        "    return {'doubl': v*3, 'ok': True}"}},
        ],
        "entrypoints": {"main": {"steps": [
            {"id": "a", "type": "call", "fn": "inline/triple@v1",
             "inputs": {"valeur": "10"}, "capture": {"doubl": "d1"},
             "next": "b"},
            {"id": "b", "type": "call", "fn": "test/double@v1",
             "inputs": {"valeur": "5"}, "capture": {"doubl": "d2"},
             "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]}},
    }
    inline = cy.inline_document(agent, kind="agent")
    # la sub-skill est attachée au step ET remontée au plus haut niveau
    assert "skill" in inline["entrypoints"]["main"]["steps"][0]
    assert inline["entrypoints"]["main"]["steps"][0]["skill"]["name"] == "inline/triple@v1"
    assert [s["name"] for s in inline.get("sub_skills", [])] == ["inline/triple@v1"]
    fsm = FSMInterpreter(bridge=None)
    r = fsm.run(workflow=inline["entrypoints"]["main"], messages=[],
                variables={}, home="/tmp")
    assert r.status == "success", r.end_reason
    assert r.variables.get("d1") == 30    # sub-skill inline
    assert r.variables.get("d2") == 10    # skill catalogue


def test_obj_call_runtime():
    """Step obj_call : appelle un objet runtime (daemon.info) dans le FSM.

    daemon.* est fail-safe : l'agent doit avoir la règle privileges, sinon
    refus."""
    from AgentFrameWork.fsm_interpreter import FSMInterpreter
    import services.api.handlers.catalogue_local as H

    # règle daemon.info pour l'agent 1
    H.op_priv_create({"token": "write_catalogue_priv",
                      "chemin_ref": "/runtime/daemon/info", "kind": "path",
                      "level": 100, "exec": "---x", "agent_id": 1})

    wf = {"steps": [
        {"id": "info", "type": "obj_call", "path": "daemon.info()",
         "capture": {"result": "info"}, "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    fsm = FSMInterpreter(bridge=None)
    r = fsm.run(workflow=wf, messages=[], variables={"agent_id": "1"}, home="/tmp")
    assert r.status == "success", r.end_reason
    info = r.variables.get("info")
    assert isinstance(info, dict) and info.get("status") == "ok", info

    # agent 2 sans règle → refusé (fail-safe)
    fsm2 = FSMInterpreter(bridge=None)
    r2 = fsm2.run(workflow=wf, messages=[], variables={"agent_id": "2"}, home="/tmp")
    assert r2.status == "failed"
    assert "accès daemon refusé" in r2.end_reason


def test_obj_call_team_chatroom_send():
    """obj_call team.chatroom.send({{msg}}) écrit réellement en BDD."""
    import json
    from AgentFrameWork.fsm_interpreter import FSMInterpreter
    from modules.sql.agents_repo import AgentsDB
    from modules.sql.workspace import WorkspaceDB

    adb = AgentsDB()
    cur = adb.conn.execute(
        "INSERT INTO agents (name, ref, role_type, occupation, variables_json) "
        "VALUES (?,?,?,?,?)",
        ("agent-obj", "agent:agent-obj", "codeur", "noncontinue",
         json.dumps({"workspace_id": "obj-res"})))
    adb.conn.commit()
    aid = adb.conn.execute(
        "SELECT agent_id FROM agents WHERE name='agent-obj'").fetchone()[0]
    adb.close()

    wdb = WorkspaceDB()
    try:
        wdb.workspaces.create("obj-res", name="Obj Res")
    except Exception:
        pass
    wdb.close()

    wf = {"steps": [
        {"id": "send", "type": "obj_call",
         "path": "team.chatroom.send({{msg}})",
         "capture": {"result": "sent"}, "next": "end"},
        {"id": "end", "type": "end", "status": "SUCCESS"},
    ]}
    fsm = FSMInterpreter(bridge=None)
    r = fsm.run(workflow=wf, messages=[],
                variables={"agent_id": str(aid), "msg": "obj-msg"}, home="/tmp")
    assert r.status == "success", r.end_reason
    assert r.variables.get("sent", {}).get("content") == "obj-msg"


def test_privilege_ask_level():
    """ask : niveau de DEMANDE séparé du droit (exec autorisé + ask requis)."""
    import services.api.handlers.catalogue_local as H

    P = {"token": "write_catalogue_priv"}
    r = H.op_priv_create({**P, "chemin_ref": "add_money", "kind": "cmd",
                          "level": 100, "exec": "---x", "ask": "human_root",
                          "agent_id": 7})
    assert r["status"] == "ok", r

    res = H.op_priv_resolve({"chemin": "add_money", "kind": "cmd",
                             "agent_id": 7})
    assert res.get("exec") == "---x"          # droit accordé
    assert res.get("ask") == "human_root"     # demande exigée

    u = H.op_priv_use({**P, "chemin": "add_money", "kind": "cmd",
                       "level": "agent", "op": "exec", "agent_id": 7})
    assert u["allowed"] is True
    assert u["ask"] == "human_root"
    assert u["ask_pending"] is True           # autorisé MAIS demande requise

    # commande simple → pas de demande
    H.op_priv_create({**P, "chemin_ref": "git", "kind": "cmd", "level": 100,
                      "exec": "---x", "agent_id": 7})
    u2 = H.op_priv_use({**P, "chemin": "git status", "kind": "cmd",
                        "level": "agent", "op": "exec", "agent_id": 7})
    assert u2["allowed"] is True and u2["ask"] == "none"
    assert u2["ask_pending"] is False

    # ask invalide refusé
    r_bad = H.op_priv_create({**P, "chemin_ref": "x", "kind": "cmd",
                              "level": 1, "exec": "---x", "ask": "bogus"})
    assert r_bad["status"] == "error"


def test_auth_ask_human_flow():
    """Flux ask_human : action autorisée (ask) → demande humaine → decide →
    grant privileges créé."""
    import services.api.handlers.catalogue_local as H
    from services.api.handlers.auth import (
        op_auth_ask_human, op_auth_decide, op_auth_list)

    P = {"token": "write_catalogue_priv"}
    H.op_priv_create({**P, "chemin_ref": "add_money", "kind": "cmd",
                      "level": 100, "exec": "---x", "ask": "human_root",
                      "agent_id": 7})
    u = H.op_priv_use({**P, "chemin": "add_money", "kind": "cmd",
                       "level": "agent", "op": "exec", "agent_id": 7})
    assert u["allowed"] is True and u["ask_pending"] is True

    r = op_auth_ask_human({"agent_id": "7", "action": "command",
                           "target": {"command": "add_money"},
                           "ask": "human_root"})
    assert r["ok"] is True, r
    rid = r["request_id"]

    l = op_auth_list({"level": "human"})
    assert l["count"] == 1, l
    assert l["requests"][0]["action"] == "command"

    d = op_auth_decide({"request_id": rid, "decision": "allow",
                        "scope": "once", "approver_id": "humain-1"})
    assert d.get("ok") is True, d

    grants = [p for p in H.op_priv_list({})["privileges"]
              if p["chemin_ref"] == "add_money"]
    assert len(grants) >= 2   # la règle initiale + le grant du decide


def test_auth_ask_root_and_supervisor():
    """ask_root octroie un grant privileged ; security_supervisor route la
    demande vers le superviseur désigné."""
    import services.api.handlers.catalogue_local as H
    from services.api.handlers.auth import (
        op_auth_ask_root, op_auth_decide, op_auth_ask_human,
        op_auth_supervisor_set, op_auth_supervisor_list)

    # 1. ask_root → grant privileged scoped agent
    r = op_auth_ask_root({"agent_id": "7", "reason": "installer"})
    assert r["ok"] is True, r
    d = op_auth_decide({"request_id": r["request_id"], "decision": "allow",
                        "scope": "once", "approver_id": "humain-1"})
    assert d.get("ok") is True, d
    c = H.op_priv_check({"chemin": "/runtime/root/7", "kind": "path",
                         "level": "agent", "op": "privileged", "agent_id": 7})
    assert c["allowed"] is True
    c8 = H.op_priv_check({"chemin": "/runtime/root/7", "kind": "path",
                          "level": "agent", "op": "privileged", "agent_id": 8})
    assert c8["allowed"] is False

    # 2. security_supervisor : refus sans désignation, route après désignation
    r_none = op_auth_ask_human({"agent_id": "7", "action": "command",
                                "target": {"command": "x"},
                                "ask": "security_supervisor"})
    assert r_none["ok"] is False
    assert op_auth_supervisor_set(
        {"supervisor_agent_id": 99, "scope": "global",
         "token": "write_catalogue"})["ok"] is True
    assert op_auth_supervisor_list({})["ok"] is True
    r_sup = op_auth_ask_human({"agent_id": "7", "action": "command",
                               "target": {"command": "x"},
                               "ask": "security_supervisor", "team_id": "5"})
    assert r_sup["ok"] is True, r_sup
    assert r_sup["pending"] == "supervisor"
    assert r_sup["supervisor_agent_id"] == 99


def test_dollar_through_inline():
    """Le $ est conservé par compile_yaml (inline) et résolu par le FSM
    au runtime (catalogue.$lib.x → test/x)."""
    from modules.agent_graph_utils import compile_yaml as cy
    from AgentFrameWork.fsm_interpreter import FSMInterpreter

    agent = {
        "name": "test/dollar_inline@v1",
        "entrypoints": {"main": {"steps": [
            {"id": "s1", "type": "set_variable", "name": "lib", "value": "test",
             "next": "s2"},
            {"id": "s2", "type": "set_variable", "name": "base", "value": "21",
             "next": "call"},
            {"id": "call", "type": "call", "fn": "catalogue.$lib.check_home@v1",
             "inputs": {"home": "{{home}}"}, "capture": {"ok": "ok"},
             "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]}},
    }
    inline = cy.inline_document(agent, kind="agent")
    fn = inline["entrypoints"]["main"]["steps"][2]["fn"]
    assert "$lib" in fn   # le $ est conservé dans l'inline (résolu au runtime)
    fsm = FSMInterpreter(bridge=None)
    r = fsm.run(workflow=inline["entrypoints"]["main"], messages=[],
                variables={}, home="/tmp")
    assert r.status == "success", r.end_reason
    assert r.variables.get("ok") is True   # $lib résolu → test/check_home@v1


def test_openai_chat_completions_translation():
    """La traduction OpenAI → swarm : model→mode, dernier user→prompt,
    format de réponse OpenAI."""
    from services.api.handlers.openai_compat import (
        _mode_from_model, _last_user_content)

    assert _mode_from_model("my-swarm-build") == "build"
    assert _mode_from_model("my-swarm") == "plan"
    msgs = [{"role": "system", "content": "x"},
            {"role": "user", "content": "le vrai prompt"}]
    assert _last_user_content(msgs) == "le vrai prompt"


def test_bench_submit_stats():
    """bench/submit persisté + bench/stats agrégé (moyenne par suite)."""
    from services.api.handlers.openai_compat import (
        op_bench_submit, op_bench_stats)

    op_bench_submit({"suite": "human_eval", "task": "t1", "score": 0.8,
                     "passed": 4, "total": 5, "token": "write_catalogue"})
    op_bench_submit({"suite": "human_eval", "task": "t2", "score": 0.6,
                     "passed": 3, "total": 5, "token": "write_catalogue"})
    r_bad = op_bench_submit({"suite": "x", "task": "t", "score": 0.1})
    assert r_bad["ok"] is False   # sans token → refus

    s = op_bench_stats({"suite": "human_eval"})
    assert s["ok"] is True
    assert s["stats"][0]["n"] == 2
    assert s["stats"][0]["avg_score"] == 0.7


def test_benchmark_runner_rapport():
    """test-benchmark-auto : rapport complet (score, usage, providers, steps,
    restrict_llm liste + budget)."""
    import services.benchmark_runner as BR
    from services.api.router import dispatch

    # mock _chat (pas de daemon)
    def fake_chat(ws, prompt, base, key, allow):
        return ("def fact(n):\n    return 1 if n <= 1 else n * fact(n-1)",
                {"provider": "google", "model": "gemini-2.5-flash",
                 "nb_req": 1, "tok_in": 60, "tok_out": 30, "dollars": 0.01})
    BR._chat = fake_chat

    r = dispatch("test-benchmark-auto", {
        "benchmark_name": "factorial", "team": "llm-code",
        "restrict_llm": "google/gemini-2.5-flash", "n": 2, "per_task": True})
    assert r["status"] == "ok", r
    assert r["workspace"] == "mw-llm-code"
    assert r["score"] == 1.0
    assert r["usage"]["nb_req"] == 2
    assert "google/gemini-2.5-flash" in r["providers"]
    assert len(r["steps"]) == 2

    # budget nb_req=1 → arrêt après 1 tâche
    r2 = dispatch("test-benchmark-auto", {
        "benchmark_name": "factorial", "team": "llm-code",
        "restrict_llm": {"nb_req": 1}, "n": 3, "per_task": True})
    assert r2["usage"]["nb_req"] == 1
    assert len(r2["steps"]) == 1
    assert any(n.get("type") == "budget" for n in r2["notes"])
