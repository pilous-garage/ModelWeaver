"""Fixtures communes pour les tests du module shell."""

import tempfile
from pathlib import Path

import pytest

from AgentsCatalogue.lib.shell import (
    Shell, ShellAuth, ShellLog, get_shell,
)
from AgentsCatalogue.lib.shell.tracker import tracker


@pytest.fixture
def home_root():
    with tempfile.TemporaryDirectory() as td:
        root = Path(td) / "home"
        root.mkdir()
        yield root


@pytest.fixture
def workdir(home_root):
    wd = home_root / "work"
    wd.mkdir()
    return str(wd)


@pytest.fixture
def auth(home_root):
    return ShellAuth(home_root=home_root)


@pytest.fixture
def shell(workdir, auth):
    s = get_shell(workdir=workdir, home_root=auth.home_root)
    yield s
    try:
        s.close()
    except Exception:
        pass


@pytest.fixture(autouse=True)
def clean_tracker():
    yield
    tracker.clear()
