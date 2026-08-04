import pytest
from AgentFrameWork.fsm import FSM, PauseManager, PauseScope, StreamBus


class FakeSSEBridge:
    def __init__(self, stream_bus: StreamBus):
        self.stream_bus = stream_bus
        self.events = []

    def publish(self, event: dict) -> None:
        if self.stream_bus.is_stream_paused():
            self.events.append({"dropped": True, "event": event})
        else:
            self.events.append({"dropped": False, "event": event})


class FakeChatRoute:
    def __init__(self, fsm: FSM):
        self.fsm = fsm
        self.responses = []

    def handle_chat(self, message: str) -> dict:
        self.responses.append({"message": message, "paused": self.fsm.is_paused()})
        return {"ok": True, "message": message}


class TestIntegrationPauseSSEAndChat:
    def test_sse_stream_paused_when_agent_paused(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        bridge = FakeSSEBridge(fsm._stream_bus)
        chat = FakeChatRoute(fsm)

        fsm.pause()
        bridge.publish({"type": "token", "data": "hello"})
        chat.handle_chat("hello")

        assert bridge.events[-1]["dropped"] is True
        assert chat.responses[-1]["paused"] is True

    def test_sse_stream_paused_when_project_paused(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        bridge = FakeSSEBridge(fsm._stream_bus)

        fsm.pause_project()
        bridge.publish({"type": "token", "data": "hello"})
        assert bridge.events[-1]["dropped"] is True

    def test_sse_stream_paused_when_team_paused(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        bridge = FakeSSEBridge(fsm._stream_bus)

        fsm.pause_team()
        bridge.publish({"type": "token", "data": "hello"})
        assert bridge.events[-1]["dropped"] is True

    def test_chat_route_not_blocked_when_stream_paused(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        chat = FakeChatRoute(fsm)

        fsm.pause()
        response = chat.handle_chat("hello")
        assert response["ok"] is True
        assert response["message"] == "hello"

    def test_stream_resumes_after_resume(self):
        pm = PauseManager()
        fsm = FSM(pause_manager=pm)
        bridge = FakeSSEBridge(fsm._stream_bus)

        fsm.pause()
        bridge.publish({"type": "token", "data": "a"})
        assert bridge.events[-1]["dropped"] is True

        fsm.resume()
        bridge.publish({"type": "token", "data": "b"})
        assert bridge.events[-1]["dropped"] is False

    def test_shared_pause_manager_status(self):
        pm = PauseManager()
        fsm1 = FSM(pause_manager=pm)
        fsm2 = FSM(pause_manager=pm)

        fsm1.pause_project()
        assert fsm2.is_paused() is True
        assert fsm2.status()["pause_manager"]["project_paused"] is True
