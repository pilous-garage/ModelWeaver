from __future__ import annotations

"""
Writer dédié du domaine score.

Service tick qui calcule et persiste les scores, buckets et benchmarks
à partir des données batch/runtime.
"""

from modules.sqlite.score import write as score_write


def tick():
    """
    Tick principal du scoreur.
    - Calcule les scores de thinking power, benchmarks, buckets
    - Persiste via score_write.*
    """
    # TODO: implémenter le calcul des scores
    return True


__all__ = ["tick"]
