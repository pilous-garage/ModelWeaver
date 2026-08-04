import json
import time
import threading
import queue
import pytest
from unittest.mock import MagicMock, patch

# ──────────────────────────────────────────────────────────────
# Helpers légers pour mocker le bridge LLM
# ──────────────────────────────────────────────────────────────

class DummyDeltaStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._i = 0
    def __iter__(self):
        return self
    def __next__(self):
        if self._i >= len(self._chunks):
            raise StopIteration
        c = self._chunks[self._i]
        self._i += 1
        return c


class DummyBridge:
    def __init__(self, response="ok", stream_chunks=None):
        self.response = response
        self.stream_chunks = stream_chunks or ["hello", " world"]
        self.calls = []

    def chat(self, provider_ref, model_ref, messages, temperature=0.7, max_tokens=4096, agent_id=None, **kwargs):
        self.calls.append(("chat", messages))
        class R:
            content = self.response
            provider_used = provider_ref
            model_used = model_ref
            fallbacks = 0
            tool_calls = None
            usage = {"total_tokens": 10}
            budget = {}
        return R()

    def chat_stream(self, provider_ref, model_ref, messages, temperature=0.7, max_tokens=4096, agent_id=None, **kwargs):
        self.calls.append(("chat_stream", messages))
        return DummyDeltaStream(self.stream_chunks)


# ──────────────────────────────────────────────────────────────
# Tests unitaires FSMInterpreter : pause/resume
# ──────────────────────────────────────────────────────────────

from AgentFrameWork.fsm_interpreter import FSMInterpreter, FSMResult, AgentAbort


def _make_fsm(bridge=None):
    return FSMInterpreter(bridge=bridge or DummyBridge())


def test_fsm_pause_before_step_and_resume():
    """Le signal_check positionne _paused ; le FSM attend puis reprend au step suivant."""
    fsm = _make_fsm()
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "s2"},
            {"id": "s2", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]

    state = {"step_seen": []}

    def signal_check(result: FSMResult):
        state["step_seen"].append(result.next_step_id or "start")
        if len(state["step_seen"]) == 1:
            result._paused = True
        elif len(state["step_seen"]) == 2:
            result._paused = False

    res = fsm.run(
        workflow,
        messages,
        signal_check=signal_check,
        provider_ref="p",
        model_ref="m",
    )
    assert len(state["step_seen"]) >= 2
    assert res.status == "success"
    assert res.iterations >= 2


def test_fsm_pause_does_not_advance_step():
    """Pendant la pause, le step courant n'est pas avancé ; après resume, on reste sur le même step."""
    fsm = _make_fsm()
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "s2"},
            {"id": "s2", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]

    state = {"paused_once": False}

    def signal_check(result: FSMResult):
        if not state["paused_once"]:
            result._paused = True
            state["paused_once"] = True
        else:
            result._paused = False

    stream_chunks = []
    def stream_sink(delta):
        stream_chunks.append(delta)

    res = fsm.run(
        workflow,
        messages,
        signal_check=signal_check,
        stream_sink=stream_sink,
        provider_ref="p",
        model_ref="m",
    )
    assert len(stream_chunks) >= 1
    assert res.status == "success"


def test_fsm_kill_signal_aborts():
    """AgentAbort dans signal_check interrompt le FSM."""
    fsm = _make_fsm()
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "s2"},
            {"id": "s2", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]

    def signal_check(result: FSMResult):
        raise AgentAbort()

    res = fsm.run(workflow, messages, signal_check=signal_check, provider_ref="p", model_ref="m")
    assert res.status == "aborted"
    assert "Interrompu" in (res.end_reason or "")


# ──────────────────────────────────────────────────────────────
# Tests unitaires AgentDaemon : lifecycle pause/resume/status
# ──────────────────────────────────────────────────────────────

from services.agent_daemon import AgentDaemon


def test_lifecycle_pause_signal_sent():
    """pause -> send_signal('pause')"""
    db = MagicMock()
    db.conn.execute.return_value.fetchone.return_value = {
        "agent_id": 1,
        "name": "a1",
        "role_type": "chat",
        "status": "IDLE",
        "config_json": "{}",
        "resources_json": "{}",
    }
    with patch("services.agent_daemon.AgentManager") as MockMgr:
        mgr = MockMgr.return_value
        mgr.send_signal.return_value = {"ok": True, "signal": "pause"}
        out = AgentDaemon.call(1, "pause")
        assert out["status"] == "ok"
        mgr.send_signal.assert_called_once_with(1, "pause")


def test_lifecycle_resume_signal_sent():
    db = MagicMock()
    db.conn.execute.return_value.fetchone.return_value = {
        "agent_id": 2,
        "name": "a2",
        "role_type": "chat",
        "status": "PAUSED",
        "config_json": "{}",
        "resources_json": "{}",
    }
    with patch("services.agent_daemon.AgentManager") as MockMgr:
        mgr = MockMgr.return_value
        mgr.send_signal.return_value = {"ok": True, "signal": "resume"}
        out = AgentDaemon.call(2, "resume")
        assert out["status"] == "ok"
        mgr.send_signal.assert_called_once_with(2, "resume")


def test_lifecycle_status_returns_agent():
    db = MagicMock()
    db.conn.execute.return_value.fetchone.return_value = {
        "agent_id": 3,
        "name": "a3",
        "role_type": "chat",
        "status": "RUNNING",
        "config_json": "{}",
        "resources_json": "{}",
    }
    with patch("services.agent_daemon.AgentManager") as MockMgr:
        mgr = MockMgr.return_value
        mgr.get_by_id.return_value = {
            "agent_id": 3,
            "name": "a3",
            "status": "RUNNING",
        }
        out = AgentDaemon.call(3, "status")
        assert out["status"] == "ok"
        assert out["agent"]["agent_id"] == 3


# ──────────────────────────────────────────────────────────────
# Tests unitaires TeamManager : pause/resume au niveau team
# ──────────────────────────────────────────────────────────────

from services.team_manager import TeamManager, Team, TeamSpec, TeamMemberSpec, TeamLeaderSpec


def _make_team_spec(name="t1", members=("m1", "m2"), leader="lead"):
    members_specs = []
    for m in members:
        members_specs.append(TeamMemberSpec(agent_name=m, role="chat", occupation="continue"))
    leader_spec = None
    if leader:
        leader_spec = TeamLeaderSpec(agent_name=leader, role="team_leader", occupation="continue")
    return TeamSpec(
        name=name,
        team_name=name,
        topology="flat",
        members=members_specs,
        team_leader=leader_spec,
        workspace_id=None,
    )


def test_team_pause_only_target_team_members():
    """Pause au niveau team ne touche que les membres de cette team."""
    mgr = TeamManager()
    mgr._teams = {}
    mgr._initialized = False
    mgr.init()

    spec = _make_team_spec(name="alpha", members=("a", "b"), leader="lead")
    team = Team(spec)
    team.status = "ready"
    team.member_agent_ids = {"a": 101, "b": 102}
    team.team_leader_agent_id = 100
    mgr._teams["alpha"] = team

    spec2 = _make_team_spec(name="beta", members=("c",), leader=None)
    team2 = Team(spec2)
    team2.status = "ready"
    team2.member_agent_ids = {"c": 201}
    team2.team_leader_agent_id = None
    mgr._teams["beta"] = team2

    team.pause = MagicMock()
    team2.pause = MagicMock()
    team.pause()
    team2.pause.assert_not_called()
    team.pause.assert_called_once()


def test_team_resume_only_target_team_members():
    mgr = TeamManager()
    mgr._teams = {}
    mgr._initialized = False
    mgr.init()

    spec = _make_team_spec(name="alpha", members=("a",), leader="lead")
    team = Team(spec)
    team.status = "paused"
    team.member_agent_ids = {"a": 101}
    team.team_leader_agent_id = 100
    mgr._teams["alpha"] = team

    spec2 = _make_team_spec(name="beta", members=("c",), leader=None)
    team2 = Team(spec2)
    team2.status = "running"
    team2.member_agent_ids = {"c": 201}
    team2.team_leader_agent_id = None
    mgr._teams["beta"] = team2

    team.resume = MagicMock()
    team2.resume = MagicMock()
    team.resume()
    team2.resume.assert_not_called()
    team.resume.assert_called_once()


def test_team_status_returns_global_and_per_level():
    mgr = TeamManager()
    mgr._teams = {}
    mgr._initialized = False
    mgr.init()

    spec = _make_team_spec(name="alpha", members=("a",), leader="lead")
    team = Team(spec)
    team.status = "running"
    team.member_agent_ids = {"a": 101}
    team.team_leader_agent_id = 100
    mgr._teams["alpha"] = team

    with patch("services.team_manager._get_agent_db") as MockDB:
        db = MockDB.return_value
        db.conn.execute.return_value.fetchone.side_effect = [
            {"status": "RUNNING", "last_active_at": "2025-01-01T00:00:00"},
            {"status": "IDLE", "last_active_at": "2025-01-01T00:00:00"},
        ]
        info = team.status_info()
        assert info["name"] == "alpha"
        assert info["status"] == "running"
        assert info["member_count"] == 1
        assert info["team_leader"]["agent_name"] == "lead"


# ──────────────────────────────────────────────────────────────
# Tests unitaires : non-blocage routes chat pendant pause
# ──────────────────────────────────────────────────────────────

from services.api.daemon import MWAPIHandler


class DummyHandler(MWAPIHandler):
    def __init__(self):
        self.headers = {}
        self.client_address = ("127.0.0.1", 9999)
        self.wfile = MagicMock()
        self.rfile = MagicMock()
        self.close_connection = False
        self.path = ""
        self.command = "GET"
        self.server = MagicMock()
        self.server.token = "tok"

    def send_response(self, code):
        self._response_code = code

    def send_header(self, k, v):
        self.headers[k] = v

    def end_headers(self):
        pass

    def _send(self, code, payload):
        self._response_code = code
        self._payload = payload


def test_chat_route_not_blocked_during_pause():
    """La route /v1/chat doit rester accessible même si l'agent est en pause."""
    handler = DummyHandler()
    handler.path = "/v1/chat"
    handler.command = "POST"
    handler.rfile.read.return_value = b'{"agent_id":1,"message":"hi"}'

    fake_res = {"status": "ok", "reply": "paused but route ok"}
    with patch.object(handler, "_authorized", return_value=True),          patch("services.api.daemon.ROUTES", {"chat": lambda p: fake_res}):
        handler.do_POST()
        assert getattr(handler, "_response_code", None) == 200
        assert handler._payload["result"] == fake_res


# ──────────────────────────────────────────────────────────────
# Tests unitaires : pause des streamings SSE (bridge/chat_stream)
# ──────────────────────────────────────────────────────────────

from services.api.daemon import STREAMING_ROUTES


def test_sse_stream_paused_by_signal_check():
    """chat_stream respecte le flag paused positionné par signal_check."""
    def stream_handler(params, wfile):
        for i in range(3):
            wfile.write(f"data: chunk{i}

".encode())
            time.sleep(0.01)
        wfile.write(b"event: done
data: {\"done\":true}

")

    handler = DummyHandler()
    handler.path = "/v1/chat_stream"
    handler.command = "POST"
    handler.rfile.read.return_value = b'{"agent_id":1}'

    with patch.object(handler, "_authorized", return_value=True),          patch("services.api.daemon.STREAMING_ROUTES", {"chat_stream": stream_handler}),          patch("services.api.daemon._get_rt"),          patch("services.api.daemon._get_mw"),          patch("services.api.daemon._get_cat"):
        handler.do_POST()
        assert getattr(handler, "_response_code", None) == 200


# ──────────────────────────────────────────────────────────────
# Tests d'intégration FSM : vérification du flag pause avant chaque step
# ──────────────────────────────────────────────────────────────

def test_integration_fsm_pause_flag_before_each_step():
    """Avant chaque step, signal_check est appelé et peut mettre en pause."""
    fsm = _make_fsm()
    workflow = {
        "steps": [
            {"id": "s1", "type": "llm_call", "next": "s2"},
            {"id": "s2", "type": "llm_call", "next": "s3"},
            {"id": "s3", "type": "llm_call", "next": "end"},
            {"id": "end", "type": "end", "status": "SUCCESS"},
        ]
    }
    messages = [{"role": "user", "content": "hi"}]

    seen = []

    def signal_check(result: FSMResult):
        seen.append(result.next_step_id or "start")
        if result.next_step_id == "s2":
            result._paused = True
        else:
            result._paused = False

    res = fsm.run(
        workflow,
        messages,
        signal_check=signal_check,
        provider_ref="p",
        model_ref="m",
    )
    assert len(seen) >= 2
    assert res.status == "success"
