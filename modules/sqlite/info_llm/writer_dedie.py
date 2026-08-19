from __future__ import annotations

"""
Writer dédié du domaine info_llm.

Service tick qui régénère les projections info_llm depuis le catalogue local.
"""

from modules.sqlite.info_llm import write as info_write


def tick():
    """
    Tick de régénération info_llm.
    Déclenche compactage si nécessaire.
    """
    # TODO: implémenter la logique de compactage périodique
    return True


__all__ = ["tick"]
