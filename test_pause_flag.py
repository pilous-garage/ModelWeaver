"""Smoke test du mécanisme de pause partagé.

Vérifie :
- PauseFlagStore : set/get/clear/efficacité hiérarchique
- build_signal_check : intégration FSM
- PauseAwareStreamWrapper : wrapper de stream
"""

from modules.control.pause_flag import PauseFlagStore, get_pause_store
from AgentFrameWork.pause_integration import (
    build_signal_check,
    PauseAwareStreamWrapper,
    PauseSignalError,
)


class DummyResult:
    def __init__(self):
        self.status = "running"
        self._paused = False
        self.end_reason = None


def test_pause_store(tmp_path):
    db = tmp_path / "pause.db"
    store = PauseFlagStore(str(db))

    # Pas de flag initialement
    assert store.effective_state("proj1", "team1", "agent1")["paused"] is False

    # Flag projet
    store.set_flag("project", "proj1", "paused", reason="maintenance")
    eff = store.effective_state("proj1", "team1", "agent1")
    assert eff["paused"] is True
    assert eff["scope"] == "project"
    assert eff["reason"] == "maintenance"

    # Flag team n'écrase pas le flag projet
    store.set_flag("team", "team1", "paused", reason="team pause")
    eff = store.effective_state("proj1", "team1", "agent1")
    assert eff["scope"] == "project"

    # Clear projet -> on voit le flag team
    store.clear_flag("project", "proj1")
    eff = store.effective_state("proj1", "team1", "agent1")
    assert eff["paused"] is True
    assert eff["scope"] == "team"

    # Clear team -> running
    store.clear_flag("team", "team1")
    eff = store.effective_state("proj1", "team1", "agent1")
    assert eff["paused"] is False

    # Agent flag seul
    store.set_flag("agent", "agent1", "paused", reason="agent pause")
    eff = store.effective_state(None, None, "agent1")
    assert eff["paused"] is True
    assert eff["scope"] == "agent"

    store.close()


def test_build_signal_check(tmp_path):
    db = tmp_path / "pause2.db"
    store = PauseFlagStore(str(db))
    store.set_flag("project", "projX", "paused", reason="pause projet")

    check = build_signal_check(project_id="projX", pause_store=store)
    result = DummyResult()
    check(result)
    assert result._paused is True
    assert result.status == "paused"
    assert result.end_reason == "pause projet"

    # Resume : clear flag
    store.clear_flag("project", "projX")
    result2 = DummyResult()
    check(result2)
    assert result2._paused is False
    assert result2.status == "running"

    store.close()


def test_pause_aware_stream_wrapper():
    events = []

    def source():
        for i in range(5):
            events.append(("src", i))
            yield f"chunk{i}"

    paused = [True, True, False]

    def pause_check():
        return paused.pop(0) if paused else False

    wrapper = PauseAwareStreamWrapper(source(), pause_check=pause_check)
    out = []
    for chunk in wrapper:
        out.append(chunk)
        events.append(("out", chunk))

    # Avec 2 pauses avant reprise, on devrait recevoir tous les chunks
    assert out == [f"chunk{i}" for i in range(5)]
    wrapper.stop()


if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        from pathlib import Path
        test_pause_store(Path(tmp))
        test_build_signal_check(Path(tmp))
    test_pause_aware_stream_wrapper()
    print("OK: pause flag tests passed")
