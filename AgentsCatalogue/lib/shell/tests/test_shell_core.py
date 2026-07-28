"""Tests du Shell state machine, ShellLog, environnement."""

from pathlib import Path

import pytest

from AgentsCatalogue.lib.shell import (
    Shell, ShellAuth, ShellLog, ShellState, get_shell, list_shells,
)


class TestShellState:
    def test_initial_created(self, shell):
        assert shell.state == ShellState.OPEN  # get_shell ouvre déjà

    def test_create_from_scratch(self, auth, workdir):
        s = Shell(shell_id="test-state", workdir=workdir, auth=auth)
        assert s.state == ShellState.CREATED
        s.open()
        assert s.state == ShellState.OPEN

    def test_suspend_resume(self, shell):
        shell.suspend()
        assert shell.state == ShellState.SUSPENDED
        shell.resume()
        assert shell.state == ShellState.OPEN

    def test_run_transition(self, shell):
        r = shell.run("echo ok")
        assert r.get("exit_code") == 0
        assert shell.state == ShellState.OPEN

    def test_close(self, shell):
        shell.close()
        assert shell.state == ShellState.CLOSED

    def test_close_idempotent(self, shell):
        shell.close()
        r = shell.close()
        assert r["status"] == "already_closed"

    def test_run_after_close(self, shell):
        shell.close()
        with pytest.raises(RuntimeError, match="dans l'état closed"):
            shell.run("echo nope")

    def test_run_suspended(self, shell):
        shell.suspend()
        r = shell.run("echo from_suspended")
        assert r.get("exit_code") == 0

    def test_multiple_runs(self, shell):
        for i in range(5):
            r = shell.run(f"echo run_{i}")
            assert r.get("exit_code") == 0

    def test_status(self, shell):
        st = shell.status()
        assert st["state"] == "open"
        assert "workdir" in st
        assert "commands_executed" in st


class TestShellWorkdir:
    def test_cd_updates_workdir(self, shell, workdir):
        Path(workdir).mkdir(parents=True, exist_ok=True)
        shell.run("mkdir sub")
        shell.run("cd sub")
        assert shell.workdir.name == "sub"

    def test_cd_nested(self, shell):
        shell.run("mkdir a")
        shell.run("cd a")
        shell.run("mkdir b")
        shell.run("cd b")
        assert shell.workdir.name == "b"

    def test_cd_outside_vfs_rejected(self, shell):
        r = shell.run("cd /tmp")
        assert r.get("exit_code") != 0


class TestShellEnv:
    def test_export_isolated(self, shell):
        import os
        shell.run("export FOO=bar")
        assert "FOO" not in os.environ

    def test_export_seen_by_env(self, shell):
        shell.run("export FOO=bar")
        r = shell.run("env")
        assert "FOO=bar" in r["stdout"]

    def test_unset(self, shell):
        shell.run("export FOO=bar")
        shell.run("unset FOO")
        r = shell.run("env")
        assert "FOO=" not in r["stdout"]

    def test_export_multiple(self, shell):
        shell.run("export A=1 B=2")
        r = shell.run("env")
        assert "A=1" in r["stdout"]
        assert "B=2" in r["stdout"]


class TestShellLog:
    def test_log_commands(self, shell):
        shell.run("echo one")
        shell.run("echo two")
        assert shell.log.command_count == 2

    def test_log_tail(self, shell):
        shell.run("echo a")
        shell.run("echo b")
        tail = shell.log.tail(1)
        assert len(tail) == 1
        assert tail[0]["cmd"] == "echo b"

    def test_save_load(self, shell, workdir):
        shell.run("echo persist")
        shell.log.save()
        assert shell.log.state_file.exists()

        log2 = ShellLog(shell.shell_id, Path(workdir))
        loaded = log2.load()
        assert loaded
        assert log2.command_count == 1


class TestListShells:
    def test_list_shells(self, shell):
        shells = list_shells(shell.auth.home_root.parent)
        found = [s for s in shells if s["shell_id"] == shell.shell_id]
        assert len(found) == 0  # pas persisté encore

    def test_list_after_save(self, shell, workdir):
        shell.log.save()
        shells = list_shells(Path(workdir))
        found = [s for s in shells if s["shell_id"] == shell.shell_id]
        assert len(found) == 1
