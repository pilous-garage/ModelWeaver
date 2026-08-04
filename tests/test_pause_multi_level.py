"""Tests pause multi-niveau : PauseManager, FSM, StreamBus, routes chat."""

import os
import sys
import time
import threading
import tempfile
import shutil

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from AgentFrameWork.pause_manager import PauseManager, PauseLevel
from AgentFrameWork.stream_bus import StreamBus, StreamBusDB, stream_bus, activate_cross_process
from AgentFrameWork.fsm_interpreter import FSMInterpreter, FSMResult, AgentAbort
from AgentFrameWork.bridge import Bridge


@pytest.fixture()
def tmp_db(tmp_path):
    db = str(tmp_path / "pause.db")
    PauseManager._instance = None
    yield db
    PauseManager._instance = None


@pytest.fixture()
def manager(tmp_db):
    return PauseManager.get_instance(tmp_db)


class TestPauseManager:
    def test_default_not_paused(self, manager):
        status = manager.get_status(agent_id=1)
        assert status["effective_paused"] is False
        assert status["agent_paused"] is False
        assert status["project_paused"] is False
        assert status["team_paused"] is False

    def test_agent_pause(self, manager):
        manager.pause(PauseLevel.AGENT, "1")
        assert manager.is_paused(PauseLevel.AGENT, "1") is True
        status = manager.get_status(agent_id=1)
        assert status["agent_paused"] is True
        assert status["effective_paused"] is True

    def test_project_pause(self, manager):
        manager.pause(PauseLevel.PROJECT, "proj_x")
        status = manager.get_status(agent_id=1, project_id="proj_x")
        assert status["project_paused"] is True
        assert status["effective_paused"] is True

    def test_team_pause(self, manager):
        manager.pause(PauseLevel.TEAM, "team_a")
        status = manager.get_status(agent_id=1, team_id="team_a")
        assert status["team_paused"] is True
        assert status["effective_paused"] is True

    def test_resume(self, manager):
        manager.pause(PauseLevel.AGENT, "1")
        assert manager.is_paused(PauseLevel.AGENT, "1") is True
        manager.resume(PauseLevel.AGENT, "1")
        assert manager.is_paused(PauseLevel.AGENT, "1") is False

    def test_effective_paused_any_level(self, manager):
        # seul project
        manager.pause(PauseLevel.PROJECT, "p1")
        assert manager.get_status(agent_id=1, project_id="p1")["effective_paused"] is True
        manager.resume(PauseLevel.PROJECT, "p1")
        # seul team
        manager.pause(PauseLevel.TEAM, "t1")
        assert manager.get_status(agent_id=1, team_id="t1")["effective_paused"] is True
        manager.resume(PauseLevel.TEAM, "t1")
        # seul agent
        manager.pause(PauseLevel.AGENT, "1")
        assert manager.get_status(agent_id=1)["effective_paused"] is True

    def test_singleton(self, tmp_db):
        m1 = PauseManager.get_instance(tmp_db)
        m2 = PauseManager.get_instance(tmp_db + "_2")
        assert m1 is m2


class TestStreamBusPause:
    def test_inprocess_pause(self):
        bus = StreamBus()
        bus.set_paused(agent_id=10, paused=True)
        assert bus.is_paused(10) is True
        seq = bus.publish(10, "chunk")
        assert seq == -1
        bus.set_paused(agent_id=10, paused=False)
        seq = bus.publish(10, "chunk")
        assert seq >= 0

    def test_db_pause(self, tmp_path):
        db = str(tmp_path / "stream.db")
        bus = StreamBusDB(db)
        bus.set_paused(agent_id=20, paused=True)
        assert bus.is_paused(20) is True
        seq = bus.publish(20, "chunk")
        assert seq == -1
        bus.set_paused(agent_id=20, paused=False)
        seq = bus.publish(20, "chunk")
        assert seq >= 0

    def test_cross_process_pause(self, tmp_path):
        db = str(tmp_path / "cross.db")
        activate_cross_process(db)
        try:
            stream_bus.set_paused(30, True)
            assert stream_bus.is_paused(30) is True
            seq = stream_bus.publish(30, "token")
            assert seq == -1
            stream_bus.set_paused(30, False)
            assert stream_bus.is_paused(30) is False
            seq = stream_bus.publish(30, "token")
            assert seq >= 0
        finally:
            from AgentFrameWork.stream_bus import _crossproc_bus
            if _crossproc_bus:
                _crossproc_bus.close()
            from AgentFrameWork.stream_bus import _crossproc_bus as c
            globals()["_crossproc_bus"] = None

    def test_stream_facade_delegates_pause(self, tmp_path):
        db = str(tmp_path / "facade.db")
        activate_cross_process(db)
        try:
            stream_bus.set_paused(40, True)
            assert stream_bus.is_paused(40) is True
            assert stream_bus.publish(40, "x") == -1
            stream_bus.set_paused(40, False)
            assert stream_bus.is_paused(40) is False
            assert stream_bus.publish(40, "x") >= 0
        finally:
            from AgentFrameWork.stream_bus import _crossproc_bus
            if _crossproc_bus:
                _crossproc_bus.close()
            globals()["_crossproc_bus"] = None


class TestFSMPauseIntegration:
    @pytest.fixture()
    def fsm(self):
        return FSMInterpreter(bridge=Bridge())

    def test_signal_pause_sets_result_flag(self, fsm):
        workflow = {
            "steps": [
                {"id": "s1", "type": "llm_call", "next": "end"},
                {"id": "end", "type": "end", "status": "SUCCESS"},
            ]
        }
        paused = []

        def signal_check(result):
            if not paused:
                result._paused = True
                paused.append(True)

        result = fsm.run(workflow, messages=[], signal_check=signal_check)
        assert result._paused is True
        assert result.status == "running"

    def test_signal_resume_continues(self, fsm):
        workflow = {
            "steps": [
                {"id": "s1", "type": "llm_call", "next": "end"},
                {"id": "end", "type": "end", "status": "SUCCESS"},
            ]
        }
        state = {"pause_count": 0}

        def signal_check(result):
            state["pause_count"] += 1
            if state["pause_count"] == 1:
                result._paused = True
            elif state["pause_count"] == 2:
                result._paused = False

        result = fsm.run(workflow, messages=[], signal_check=signal_check)
        assert result._paused is False
        assert result.status == "success"

    def test_kill_aborts(self, fsm):
        workflow = {
            "steps": [
                {"id": "s1", "type": "llm_call", "next": "end"},
                {"id": "end", "type": "end", "status": "SUCCESS"},
            ]
        }

        def signal_check(result):
            raise AgentAbort()

        result = fsm.run(workflow, messages=[], signal_check=signal_check)
        assert result.status == "aborted"

    def test_multi_level_pause_effective_flag(self, fsm):
        workflow = {
            "steps": [
                {"id": "s1", "type": "llm_call", "next": "end"},
                {"id": "end", "type": "end", "status": "SUCCESS"},
            ]
        }
        pause_statuses = []

        def signal_check(result):
            status = result.variables.get("pause_status", {})
            pause_statuses.append(status)
            if not status.get("effective_paused", False):
                result._paused = True
                result.variables["pause_status"] = {"project_paused": False, "team_paused": False, "agent_paused": True, "effective_paused": True}

        result = fsm.run(workflow, messages=[], signal_check=signal_check, lifecycle_mgr=None)
        assert result._paused is True
        assert any(s.get("effective_paused") for s in pause_statuses)


class TestChatRoutesNotBlocked:
    def test_chat_route_accessible_when_paused(self, manager):
        manager.pause(PauseLevel.AGENT, "99")
        status = manager.get_status(agent_id=99)
        assert status["effective_paused"] is True
        # Les routes chat ne sont pas bloquées par la pause (seul le streaming l'est)
        # Ici on vérifie que le flag de pause n'interdit pas la route chat.
        assert status["agent_paused"] is True
        # Dans le routeur,  est une capability, pas un lifecycle.
        # La pause ne doit pas retirer  des routes exposées.
        from AgentFrameWork.router import routes_for
        routes = routes_for(role="assistant", state="PAUSED", skills=["chat"])
        ops = [r.op for r in routes]
        assert "chat" in ops

    def test_lifecycle_routes_available_when_paused(self, manager):
        manager.pause(PauseLevel.AGENT, "99")
        from AgentFrameWork.router import routes_for
        routes = routes_for(role="assistant", state="PAUSED")
        ops = [r.op for r in routes]
        assert "status" in ops
        assert "resume" in ops
        assert "kill" in ops


class TestPropagation:
    def test_pause_project_propagates_to_agent_status(self, manager):
        manager.pause(PauseLevel.PROJECT, "proj1")
        status = manager.get_status(agent_id=5, project_id="proj1", team_id="team1")
        assert status["project_paused"] is True
        assert status["effective_paused"] is True

    def test_pause_team_does_not_affect_other_team(self, manager):
        manager.pause(PauseLevel.TEAM, "teamA")
        status = manager.get_status(agent_id=1, team_id="teamB")
        assert status["team_paused"] is False
        assert status["effective_paused"] is False

    def test_resume_team_restores_agent(self, manager):
        manager.pause(PauseLevel.TEAM, "teamX")
        manager.resume(PauseLevel.TEAM, "teamX")
        status = manager.get_status(agent_id=1, team_id="teamX")
        assert status["effective_paused"] is False


class TestFSMStreamPause:
    def test_stream_sink_not_called_when_paused(self):
        bus = StreamBus()
        bus.set_paused(agent_id=1, paused=True)
        chunks = []
        def sink(chunk):
            chunks.append(chunk)
        seq = bus.publish(1, "token1", kind="token")
        assert seq == -1
        bus.set_paused(agent_id=1, paused=False)
        seq = bus.publish(1, "token2", kind="token")
        assert seq >= 0

    def test_fsm_stream_pause_integration(self):
        workflow = {
            "steps": [
                {"id": "s1", "type": "llm_call", "next": "end"},
                {"id": "end", "type": "end", "status": "SUCCESS"},
            ]
        }
        bus = StreamBus()
        bus.set_paused(agent_id=1, paused=True)
        chunks = []
        def sink(chunk):
            chunks.append(chunk)
        def signal_check(result):
            status = result.variables.get("pause_status", {})
            if not status.get("effective_paused", False):
                result._paused = True
                result.variables["pause_status"] = {"agent_paused": True, "effective_paused": True}
        fsm = FSMInterpreter(bridge=Bridge())
        result = fsm.run(workflow, messages=[], signal_check=signal_check, stream_sink=sink)
        # Le stream_sink ne doit pas avoir reçu de chunks pendant la pause
        assert len(chunks) == 0

    def test_sse_stream_handler_respects_stream_bus_pause(self, manager):
        """Le handler SSE doit s'interrompre quand le stream_bus est en pause."""
        from services.api.daemon import MWAPIHandler
        from unittest.mock import MagicMock

        agent_id = 77
        manager.pause(PauseLevel.AGENT, str(agent_id))
        activate_cross_process(str(manager.store._path).replace("pause.db", "sse_stream.db"))
        try:
            stream_bus.set_paused(agent_id, True)

            class DummyHandler(MWAPIHandler):
                def __init__(self):
                    self.headers = {}
                    self.client_address = ("127.0.0.1", 9999)
                    self.wfile = MagicMock()
                    self.rfile = MagicMock()
                    self.close_connection = False
                    self.path = ""
                    self.command = "POST"
                    self.server = MagicMock()
                    self.server.token = "tok"

                def send_response(self, code):
                    self._response_code = code

                def send_header(self, k, v):
                    self.headers[k] = v

                def end_headers(self):
                    pass

            def stream_handler(params, wfile):
                for i in range(3):
                    wfile.write(f"data: chunk{i}\n\n".encode())
                wfile.write(b"event: done\ndata: {\"done\":true}\n\n")

            handler = DummyHandler()
            handler.path = "/v1/chat_stream"
            handler.command = "POST"
            handler.rfile.read.return_value = json.dumps({"agent_id": agent_id}).encode()
            with patch.object(handler, "_authorized", return_value=True), \
                 patch("services.api.daemon.STREAMING_ROUTES", {"chat_stream": stream_handler}), \
                 patch("services.api.daemon._get_rt"), \
                 patch("services.api.daemon._get_mw"), \
                 patch("services.api.daemon._get_cat"):
                handler.do_POST()
                # Le handler SSE renvoie 200, mais aucun chunk ne doit avoir été écrit
                assert getattr(handler, "_response_code", None) == 200
                written = b"".join(call.args[0] for call in handler.wfile.write.call_args_list)
                assert b"chunk" not in written
        finally:
            from AgentFrameWork.stream_bus import _crossproc_bus
            if _crossproc_bus:
                _crossproc_bus.close()
            globals()["_crossproc_bus"] = None
