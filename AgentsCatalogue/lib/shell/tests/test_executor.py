"""Tests de l'executor : pipeline, redirections, expansion, fallback."""

import os
from pathlib import Path

import pytest


class TestPipeline:
    def test_simple_pipe(self, shell):
        r = shell.run("echo hello world")
        assert r["exit_code"] == 0

    def test_pipe_redirect(self, shell):
        shell.run("write data.txt a\nb\nc")
        r = shell.run("sort data.txt")
        assert r["exit_code"] == 0


class TestRedirects:
    def test_stdout_redirect(self, shell, workdir):
        r = shell.run("echo hello > out.txt")
        assert r["exit_code"] == 0
        assert r["stdout"] == ""  # stdout vidé par la redirection
        f = Path(workdir) / "out.txt"
        assert f.exists()
        assert f.read_text().strip() == "hello"

    def test_stdout_append(self, shell, workdir):
        shell.run("echo line1 > out.txt")
        shell.run("echo line2 >> out.txt")
        f = Path(workdir) / "out.txt"
        assert f.read_text().strip().split() == ["line1", "line2"]

    def test_stdin_redirect(self, shell):
        shell.run("write in.txt hello\nworld\nfoo")
        r = shell.run("wc -l < in.txt")
        assert r["exit_code"] == 0
        assert '3' in r["stdout"]


class TestVarExpansion:
    def test_expand_var(self, shell):
        shell.run("export MSG=hello")
        r = shell.run("echo $MSG")
        assert r["stdout"].strip() == "hello"

    def test_expand_brace(self, shell):
        shell.run("export NAME=World")
        r = shell.run("echo ${NAME}")
        assert r["stdout"].strip() == "World"

    def test_expand_path(self, shell):
        r = shell.run("echo $PATH")
        assert r["exit_code"] == 0
        assert "/" in r["stdout"]

    def test_literal_dollar(self, shell):
        r = shell.run("echo $$")
        assert r["stdout"] == "$\n"


class TestFallback:
    def test_unknown_builtin_defaults_to_subprocess(self, shell):
        r = shell.run("which python3")
        assert r["exit_code"] == 0

    def test_whitelist_block(self, shell):
        r = shell.run("nmap")
        assert r["exit_code"] == 126  # commande non autorisée
