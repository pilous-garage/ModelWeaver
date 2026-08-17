"""paths — résolution des chemins de DB par domaine (helper partagé).

mw_home() lit MODELWEAVER_HOME / MW_HOME (ou ~/.modelweaver).
db_path(name) -> <mw_home>/<name>.db.
"""

from __future__ import annotations

import os
from pathlib import Path


def mw_home() -> Path:
    env = os.environ.get("MODELWEAVER_HOME") or os.environ.get("MW_HOME")
    if env:
        return Path(env)
    return Path.home() / ".modelweaver"


def db_path(name: str) -> Path:
    return mw_home() / f"{name}.db"
