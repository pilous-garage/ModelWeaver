"""Pause temporelle.

Migrée depuis services/skill_manager.py (_exec_sleep).
"""

import time


def sleep(inputs: dict, ws: str) -> dict:
    seconds = float(inputs.get("seconds", 0))
    if seconds < 0:
        seconds = 0
    if seconds > 3600:
        seconds = 3600
    time.sleep(seconds)
    return {"ok": True, "slept": seconds}


__skills__ = ["sleep"]
