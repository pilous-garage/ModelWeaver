from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.score import get_writer, WRITE_SCORE_TOKEN


def upsert_model_bucket_counts(
    provider_ref: str,
    model_ref: str,
    adresse_id: Optional[int],
    **counts: Any,
) -> None:
    db = get_writer(WRITE_SCORE_TOKEN)
    with db.in_write():
        data = {
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            **counts,
        }
        db.table("model_bucket_counts").upsert(
            where={"provider_ref": provider_ref, "model_ref": model_ref},
            data=data,
        )


def upsert_score_batch(
    provider_ref: str,
    model_ref: str,
    adresse_id: Optional[int],
    **fields: Any,
) -> None:
    db = get_writer(WRITE_SCORE_TOKEN)
    with db.in_write():
        data = {
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            **fields,
        }
        db.table("score_batch").upsert(
            where={"provider_ref": provider_ref, "model_ref": model_ref},
            data=data,
        )


def upsert_score_batch_block(
    table: str,
    bucket: int,
    provider_ref: str,
    model_ref: str,
    adresse_id: Optional[int],
    requests: int,
    fail_count: int,
    total_latency_ms: float,
) -> None:
    db = get_writer(WRITE_SCORE_TOKEN)
    with db.in_write():
        db.table(table).upsert(
            where={"bucket": bucket, "provider_ref": provider_ref, "model_ref": model_ref},
            data={
                "bucket": bucket,
                "provider_ref": provider_ref,
                "model_ref": model_ref,
                "adresse_id": adresse_id,
                "requests": requests,
                "fail_count": fail_count,
                "total_latency_ms": total_latency_ms,
            },
        )


def upsert_score_benchmark_etire(
    model_ref: str,
    score_global: float,
    score_etire: float,
    **extra: Any,
) -> None:
    db = get_writer(WRITE_SCORE_TOKEN)
    with db.in_write():
        data = {
            "model_ref": model_ref,
            "score_global": score_global,
            "score_etire": score_etire,
            **extra,
        }
        db.table("score_benchmark_etire").upsert(
            where={"model_ref": model_ref},
            data=data,
        )


def upsert_score_thinking_power(model_id: int, score_thinking: float, samples: int) -> None:
    db = get_writer(WRITE_SCORE_TOKEN)
    with db.in_write():
        db.table("score_thinking_power").upsert(
            where={"model_id": model_id},
            data={
                "model_id": model_id,
                "score_thinking": score_thinking,
                "samples": samples,
            },
        )


__all__ = [
    "upsert_model_bucket_counts",
    "upsert_score_batch",
    "upsert_score_batch_block",
    "upsert_score_benchmark_etire",
    "upsert_score_thinking_power",
]
