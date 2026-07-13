#!/usr/bin/env python3
"""Service `watch_sysstate` — met à jour et publie l'état système périodiquement."""
import sys
import time
import json

from services._common import acquire_instance_lock
from modules.checker.checker import Checker


def get_system_state() -> dict:
    """Construit l'état système complet (OS, managers, hardware)."""
    checker = Checker()
    info = checker.get_system_info()
    managers = checker.get_detected_managers()
    state = {
        "os": info.get("os"),
        "os_version": info.get("os_version"),
        "architecture": info.get("architecture"),
        "processor": info.get("processor"),
        "detected_managers": managers,
    }
    state.update(checker.get_hardware_info())
    return state


def watch_system_state(interval: float = 2.0):
    """Boucle : met à jour system_state puis écrit l'état courant en JSON sur
    stdout (une ligne = un état). Un seul processus (single-instance)."""
    if not acquire_instance_lock("watch_sysstate"):
        return
    while True:
        try:
            print(json.dumps(get_system_state()), flush=True)
        except Exception as e:
            print(json.dumps({"error": str(e)}), flush=True)
        time.sleep(interval)


if __name__ == "__main__":
    from services.depends import require_deps
    from pathlib import Path
    if not require_deps(Path(__file__).resolve().parent):
        sys.exit(3)
    watch_system_state()
