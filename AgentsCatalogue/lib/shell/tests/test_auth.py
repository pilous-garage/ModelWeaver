"""Tests de ShellAuth, VFS, whitelist, authorization requests."""

from pathlib import Path
import pytest

from AgentsCatalogue.lib.shell.auth import ShellAuth, VFSPathError
from AgentsCatalogue.lib.shell.auth_request import (
    AuthorizationRequest, RequestType, request_handler,
)


class TestVFSPath:
    def test_allowed_path(self, home_root):
        auth = ShellAuth(home_root=home_root)
        valid = auth.check_path(home_root / "file.txt")
        assert valid == (home_root / "file.txt").resolve()

    def test_outside_vfs_raises(self, home_root):
        auth = ShellAuth(home_root=home_root)
        with pytest.raises(VFSPathError):
            auth.check_path(Path("/tmp"))

    def test_subdirectory(self, home_root):
        auth = ShellAuth(home_root=home_root)
        sub = home_root / "sub" / "deep"
        sub.mkdir(parents=True)
        valid = auth.check_path(sub / "f.txt")
        assert valid == (sub / "f.txt").resolve()

    def test_is_within_home(self, home_root):
        auth = ShellAuth(home_root=home_root)
        assert auth.is_within_home(home_root / "x.txt")
        assert not auth.is_within_home(Path("/tmp"))


class TestWhitelist:
    def test_default_allowed(self, home_root):
        auth = ShellAuth(home_root=home_root)
        assert auth.is_command_allowed("cat")
        assert auth.is_command_allowed("grep")
        assert auth.is_command_allowed("echo")

    def test_custom_allowed(self, home_root):
        auth = ShellAuth(home_root=home_root, allowed_commands={"mycmd"})
        assert auth.is_command_allowed("mycmd")
        assert not auth.is_command_allowed("cat")

    def test_blocked_command(self, home_root):
        auth = ShellAuth(home_root=home_root)
        assert not auth.is_command_allowed("nmap")
        assert not auth.is_command_allowed("curl")


class TestPkillAuth:
    def test_self_always_allowed(self, home_root):
        auth = ShellAuth(home_root=home_root, agent_id="agent-1", team_id="team-a", role="member")
        assert auth.can_kill_process(1234, "agent-1", "team-a")

    def test_other_agent_blocked_for_member(self, home_root):
        auth = ShellAuth(home_root=home_root, agent_id="agent-1", team_id="team-a", role="member")
        assert not auth.can_kill_process(1234, "agent-2", "team-a")

    def test_leader_can_kill_team(self, home_root):
        auth = ShellAuth(home_root=home_root, agent_id="leader-1", team_id="team-a", role="leader")
        assert auth.can_kill_process(1234, "agent-2", "team-a")

    def test_leader_cannot_kill_other_team(self, home_root):
        auth = ShellAuth(home_root=home_root, agent_id="leader-1", team_id="team-a", role="leader")
        assert not auth.can_kill_process(1234, "agent-3", "team-b")

    def test_assert_can_kill_raises(self, home_root):
        auth = ShellAuth(home_root=home_root, agent_id="agent-1", team_id="team-a", role="member")
        with pytest.raises(Exception):
            auth.assert_can_kill(1234, "agent-2", "team-a")


class TestAuthorizationRequest:
    def setup_method(self):
        request_handler.clear()

    def test_submit_and_approve(self):
        req = AuthorizationRequest("agent-1", "team-a", "pskill", {"pid": 1234})
        request_handler.submit(req)
        assert req.status.value == "pending"
        request_handler.approve(req.request_id, "leader-1")
        assert req.status.value == "approved"
        assert req.approver_id == "leader-1"

    def test_submit_and_deny(self):
        req = AuthorizationRequest("agent-1", "team-a", "pskill", {"pid": 1234})
        request_handler.submit(req)
        request_handler.deny(req.request_id, "leader-1", "not allowed")
        assert req.status.value == "denied"
        assert req.rejection_reason == "not allowed"

    def test_pending_user_type(self):
        req = AuthorizationRequest("agent-1", "team-a", "install_tool",
                                   {"tool": "nmap"}, request_type=RequestType.PENDING_USER)
        request_handler.submit(req)
        pending = request_handler.get_pending_user_authorizations()
        assert len(pending) == 1
        assert req.continue_anyway

    def test_live_type(self):
        req = AuthorizationRequest("agent-1", "team-a", "pskill",
                                   {"pid": 1234}, request_type=RequestType.LIVE)
        request_handler.submit(req)
        pending = request_handler.get_pending(agent_id="agent-1")
        assert len(pending) == 1
        assert not req.continue_anyway

    def test_get_pending_filters(self):
        req1 = AuthorizationRequest("agent-1", "team-a", "pskill", {"pid": 1})
        req2 = AuthorizationRequest("agent-2", "team-a", "pskill", {"pid": 2})
        request_handler.submit(req1)
        request_handler.submit(req2)
        assert len(request_handler.get_pending(agent_id="agent-1")) == 1
        assert len(request_handler.get_pending(agent_id="agent-2")) == 1
        assert len(request_handler.get_pending(team_id="team-a")) == 2

    def test_expiry(self):
        import time
        req = AuthorizationRequest("agent-1", "team-a", "pskill", {"pid": 1}, ttl=0)
        time.sleep(0.001)
        request_handler.submit(req)
        assert req.is_expired
        # get_pending devrait l'exclure
        pending = request_handler.get_pending(agent_id="agent-1")
        assert len(pending) == 0
