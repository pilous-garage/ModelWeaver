"""usage_batcher — SHIM de compatibilité.

La logique réelle du batcher vit désormais dans le domaine sqlite :
`modules/sqlite/batch/writer_dedie.py` (writer dédié du domaine batch).
Tout le SQL d'accès aux tables est dans `modules/sqlite/batch/{read,write}.py`
et `modules/sqlite/runtime_llm/{read,write}.py` — aucun SQL ici.

Ce module ne fait que ré-exporter les symboles historiques pour ne pas
casser les imports existants (score_blocks, score_buckets, api/handlers/usage).
"""

from __future__ import annotations

from modules.sqlite.batch.writer_dedie import (  # noqa: F401
    BatchWriter,
    get_writer_dedie,
    tick,
    main,
)

# Constantes du writer (attributs de classe réexportés pour compat).
BATCH_MARGIN_SECONDS = BatchWriter.BATCH_MARGIN_SECONDS
CASCADE = BatchWriter.CASCADE


def run_once() -> dict:
    """Point d'entrée du cycle batch (un tick)."""
    return get_writer_dedie().run_once()


def reconcile(start_ts: int, end_ts: int) -> dict:
    """Réconciliation explicite d'un timeframe [start_ts, end_ts]."""
    return get_writer_dedie().reconcile(start_ts, end_ts)


# Anciens symboles utilisés par l'API (services/api/handlers/usage.py).
# Ils pointent désormais sur le writer dédié du domaine batch ; les helpers
# _cat_conn/_rt_conn (ancien catalogue/runtime sql_old) sont dépréciés.


def _cat_conn():
    """DÉPRÉCIÉ — le catalogue n'est plus lu par le batcher (refs directes)."""
    raise RuntimeError(
        "usage_batcher._cat_conn est déprécié : le batcher lit "
        "runtime_llm.model_call_log via modules.sqlite.runtime_llm.read")


def _rt_conn():
    """DÉPRÉCIÉ — voir _cat_conn."""
    raise RuntimeError(
        "usage_batcher._rt_conn est déprécié : utiliser "
        "modules.sqlite.batch.writer_dedie.run_once")


def _reconcile_archive(cat, rt, start_ts: int, end_ts: int):
    """DÉPRÉCIÉ — utilisez batch.writer_dedie.reconcile(start, end)."""
    return reconcile(start_ts, end_ts)


if __name__ == "__main__":
    main()
