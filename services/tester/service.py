#!/usr/bin/env python3
"""Service `tester` — Testeur : lit un script d'actions install/uninstall et enfile les jobs.

Wrapper runnable. La logique vit encore dans gui_helper (dépendance déclarée
dans _contract/dependencies.py) ; elle sera décomposée ici ultérieurement.
"""
import sys
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(_REPO_ROOT, "projetadmin", "gui-main"))
from gui_helper import run_tester_service


if __name__ == "__main__":
    run_tester_service()
