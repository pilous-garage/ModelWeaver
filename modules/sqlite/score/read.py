from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.score import db_ro


def get_model_bucket_counts(
    provider_ref: str,
    model_ref: str,
) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("model_bucket_counts").select(
        where={"provider_ref": provider_ref, "model_ref": model_ref}
    )
    return rows[0] if rows else None


def get_score_batch(
    provider_ref: str,
    model_ref: str,
) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("score_batch").select(
        where={"provider_ref": provider_ref, "model_ref": model_ref}
    )
    return rows[0] if rows else None


def get_score_benchmark_etire(model_ref: str) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("score_benchmark_etire").select(where={"model_ref": model_ref})
    return rows[0] if rows else None


def list_score_batch_blocks(table: str, provider_ref: str, model_ref: str, limit: int = 100) -> List[Dict[str, Any]]:
    db = db_ro()
    return db.table(table).select(
        where={"provider_ref": provider_ref, "model_ref": model_ref},
        limit=limit,
        order_by="bucket DESC",
    )


def get_score_thinking_power(model_id: int) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("score_thinking_power").select(where={"model_id": model_id})
    return rows[0] if rows else None


__all__ = [
    "get_model_bucket_counts",
    "get_score_batch",
    "get_score_benchmark_etire",
    "list_score_batch_blocks",
    "get_score_thinking_power",
]
