#!/usr/bin/env python3
"""Service `watch_installed` — Watcher : scanne périodiquement les outils installés (cache état).

Wrapper runnable. La logique vit encore dans gui_helper (dépendance déclarée
dans _contract/dependencies.py) ; elle sera décomposée ici ultérieurement.
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "projetadmin", "gui-main"))
from gui_helper import watch_installed_tools


if __name__ == "__main__":
    watch_installed_tools()
