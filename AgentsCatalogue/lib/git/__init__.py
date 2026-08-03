"""Module git — opérations git du dépôt central + git-lite.

Ré-exports les fonctions historiques de `git_ops.py` (références de skills
`git.git_clone`, `git.git_commit`, …) et expose `lite` (`git.lite.exec`).
Le fichier historique `git.py` a été renommé `git_ops.py` pour lever le
shadowing entre le module `git.py` et ce package `git/`.
"""

from AgentsCatalogue.lib import register_alias
from AgentsCatalogue.lib.git_ops import *  # noqa: F401,F403
from AgentsCatalogue.lib.git_ops import __skills__  # noqa: F401

from . import lite as lite  # noqa: F401

# Aliens git.git_* -> git_ops.git_* (rétro-compat des références de skills)
for _name in __skills__:
    register_alias(f"git.{_name}", f"git_ops.{_name}")
