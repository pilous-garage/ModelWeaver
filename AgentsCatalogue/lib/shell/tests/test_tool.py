"""Tests de l'interface LLM tool."""

from AgentsCatalogue.lib.shell.tool import execute_cmd, execute_cmd_pending


class TestExecuteCmd:
    def test_success(self, shell):
        result = execute_cmd(shell, "echo tool_test")
        assert result["exit_code"] == 0
        assert "tool_test" in result["stdout"]

    def test_exit_code(self, shell):
        result = execute_cmd(shell, "false")
        assert result["exit_code"] == 1

    def test_structure(self, shell):
        result = execute_cmd(shell, "echo ok")
        assert set(result.keys()) == {"exit_code", "stdout", "stderr", "status", "error"}

    def test_error_status(self, shell):
        result = execute_cmd(shell, "cd /tmp")
        assert result["exit_code"] != 0


class TestExecuteCmdPending:
    def test_unknown_request(self):
        result = execute_cmd_pending(None, "nonexistent")
        assert result is not None
        assert result["status"] == "error"
