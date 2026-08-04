import pytest
from AgentFrameWork.fsm import FSM, PauseManager, PauseScope, StreamBus


class TestPauseManager:
    def test_initial_state(self):
        pm = PauseManager()
        assert pm.is_globally_paused() is False
        assert pm.is_paused(PauseScope.AGENT) is False
        assert pm.is_paused(PauseScope.PROJECT) is False
        assert pm.is_paused(PauseScope.TEAM) is False

    def test_set_and_check_agent_pause(self):
        pm = PauseManager()
        pm.set_paused(PauseScope.AGENT, True)
        assert pm.is_paused(PauseScope.AGENT) is True
        assert pm.is_globally_paused() is True

    def test_set_and_check_project_pause(self):
        pm = PauseManager()
        pm.set_paused(PauseScope.PROJECT, True)
        assert pm.is_paused(PauseScope.PROJECT) is True
        assert pm.is_globally_paused() is True

    def test_set_and_check_team_pause(self):
        pm = PauseManager()
        pm.set_paused(PauseScope.TEAM, True)
        assert pm.is_paused(PauseScope.TEAM) is True
        assert pm.is_globally_paused() is True

    def test_global_pause_when_any_level_active(self):
        pm = PauseManager()
        pm.set_paused(PauseScope.AGENT, True)
        assert pm.is_globally_paused() is True
        pm.set_paused(PauseScope.AGENT, False)
        pm.set_paused(PauseScope.PROJECT, True)
        assert pm.is_globally_paused() is True
        pm.set_paused(PauseScope.PROJECT, False)
        pm.set_paused(PauseScope.TEAM, True)
        assert pm.is_globally_paused() is True

    def test_status_dict(self):
        pm = PauseManager()
        pm.set_paused(PauseScope.AGENT, True)
        status = pm.status()
        assert status["agent_paused"] is True
        assert status["project_paused"] is False
        assert status["team_paused"] is False
        assert status["global_paused"] is True


class TestStreamBus:
    def test_initial_state(self):
        pm = PauseManager()
        bus = StreamBus(pm)
        assert bus.is_stream_paused() is False
        assert bus.status()["paused_streams"] == 0

    def test_pause_stream(self):
        pm = PauseManager()
        bus = StreamBus(pm)
        bus.pause_stream()
        assert bus.is_stream_paused() is True
        assert bus.status()["paused_streams"] == 1

    def test_resume_stream(self):
        pm = PauseManager()
        bus = StreamBus(pm)
        bus.pause_stream()
        bus.resume_stream()
        assert bus.is_stream_paused() is False
        assert bus.status()["paused_streams"] == 0

    def test_stream_paused_by_global_pause(self):
        pm = PauseManager()
        bus = StreamBus(pm)
        pm.set_paused(PauseScope.PROJECT, True)
        assert bus.is_stream_paused() is True
        assert bus.status()["stream_paused"] is True

    def test_multiple_pause_resume(self):
        pm = PauseManager()
        bus = StreamBus(pm)
        bus.pause_stream()
        bus.pause_stream()
        assert bus.status()["paused_streams"] == 2
        bus.resume_stream()
        assert bus.status()["paused_streams"] == 1
        assert bus.is_stream_paused() is True


class TestFSM:
    def test_initial_not_paused(self):
        fsm = FSM()
        assert fsm.is_paused() is False
        assert fsm.paused is False

    def test_pause_sets_agent_flag_and_stream(self):
        fsm = FSM()
        fsm.pause()
        assert fsm.is_paused() is True
        assert fsm.paused is True
        assert fsm._pause_manager.is_paused(PauseScope.AGENT) is True
        assert fsm._stream_bus.is_stream_paused() is True

    def test_resume_clears_agent_flag_and_stream(self):
        fsm = FSM()
        fsm.pause()
        fsm.resume()
        assert fsm.is_paused() is False
        assert fsm.paused is False
        assert fsm._pause_manager.is_paused(PauseScope.AGENT) is False
        assert fsm._stream_bus.is_stream_paused() is False

    def test_project_pause_propagates(self):
        fsm = FSM()
        fsm.pause_project()
        assert fsm.is_paused() is True
        assert fsm._pause_manager.is_paused(PauseScope.PROJECT) is True

    def test_team_pause_propagates(self):
        fsm = FSM()
        fsm.pause_team()
        assert fsm.is_paused() is True
        assert fsm._pause_manager.is_paused(PauseScope.TEAM) is True

    def test_agent_pause_does_not_block_other_levels(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        fsm.pause()
        assert fsm.is_paused() is True
        pm.set_paused(PauseScope.PROJECT, False)
        assert fsm._pause_manager.is_globally_paused() is True

    def test_run_step_when_paused(self):
        fsm = FSM()
        fsm.pause()
        result = fsm.run_step({"id": "step1"})
        assert result["status"] == "paused"
        assert result["step"] == "step1"

    def test_run_step_when_not_paused(self):
        fsm = FSM()
        result = fsm.run_step({"id": "step1"})
        assert result["status"] == "ok"
        assert result["step"] == "step1"

    def test_status_includes_all_levels(self):
        fsm = FSM()
        fsm.pause_project()
        status = fsm.status()
        assert status["fsm_paused"] is False
        assert status["pause_manager"]["project_paused"] is True
        assert status["stream_bus"]["stream_paused"] is True
