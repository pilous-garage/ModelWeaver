"""Tests du ProcessTracker."""

from AgentsCatalogue.lib.shell.tracker import tracker


class TestProcessTracker:
    def setup_method(self):
        tracker.clear()

    def test_register_and_get(self):
        tracker.register(1234, "agent-1", "team-a", "sleep 100")
        info = tracker.get(1234)
        assert info is not None
        assert info["agent_id"] == "agent-1"
        assert info["team_id"] == "team-a"
        assert info["cmd"] == "sleep 100"

    def test_get_nonexistent(self):
        assert tracker.get(99999) is None

    def test_processes_by_agent(self):
        tracker.register(1, "agent-1", "team-a", "cmd1")
        tracker.register(2, "agent-1", "team-a", "cmd2")
        tracker.register(3, "agent-2", "team-a", "cmd3")
        procs = tracker.processes_by_agent("agent-1")
        assert len(procs) == 2

    def test_processes_by_team(self):
        tracker.register(1, "a", "team-x", "c1")
        tracker.register(2, "b", "team-y", "c2")
        procs = tracker.processes_by_team("team-x")
        assert len(procs) == 1

    def test_all(self):
        tracker.register(1, "a", "t", "c")
        tracker.register(2, "b", "t", "c")
        assert len(tracker.all()) == 2

    def test_kill(self):
        tracker.register(1, "a", "t", "c")
        assert tracker.kill(1)
        assert not tracker.kill(1)

    def test_clear(self):
        tracker.register(1, "a", "t", "c")
        tracker.register(2, "a", "t", "c")
        tracker.clear()
        assert len(tracker.all()) == 0
