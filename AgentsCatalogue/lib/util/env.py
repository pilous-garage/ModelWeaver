"""Lecture de variables d'environnement (allowlist).

Migrée depuis services/skill_manager.py (_exec_get_env). L'allowlist est
reproduite à l'identique en variable module-level.
"""

import os

_ENV_ALLOWLIST = ("PATH", "HOME", "USER", "LOGNAME", "LANG", "LC_ALL",
                  "MODELWEAVER_HOME", "TERM", "SHELL", "PWD", "OS")


def get_env(inputs: dict, ws: str) -> dict:
    name = inputs.get("name", "")
    if name not in _ENV_ALLOWLIST:
        return {"value": "", "allowed": False}
    return {"value": os.environ.get(name, ""), "allowed": True}


__skills__ = ["get_env"]
