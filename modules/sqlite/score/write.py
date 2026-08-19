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


def ensure_adress(db, adress_id: int) -> None:
    db.table("score_adress").upsert(
        {"adress_id": adress_id, "score_init": 1.0, "score_current": 1.0},
        conflict_cols=["adress_id"], token=db._write_token,
    )


def ensure_model(db, model_ref: str) -> None:
    db.table("score_model").upsert(
        {"model_ref": model_ref, "score_init": 1.0, "score_current": 1.0},
        conflict_cols=["model_ref"], token=db._write_token,
    )


def adjust_score(db, target_kind: str, target_ref: str, bonus: float = 0.0,
                 domaine: str = "general", niveau: str = "all",
                 reason: str = "") -> Dict[str, Any]:
    """Applique un bonus/malus d'expérience et consolide score_current."""
    tok = db._write_token
    with db.in_write():
        # 1. trace l'ajustement (append, historique conservé)
        db.table("score_adjust").add(
            {"target_kind": target_kind, "target_ref": target_ref,
             "domaine": domaine, "niveau": niveau, "bonus": bonus,
             "reason": reason}, token=tok)
        # 2. consolide la table score (init 1.0, current ajusté)
        if target_kind == "adress":
            tid = int(target_ref)
            ensure_adress(db, tid)
            db._conn.execute(
                "UPDATE score_adress SET score_current = score_current + ?, "
                "samples = samples + 1, updated_at = strftime('%s','now') "
                "WHERE adress_id = ?", (bonus, tid))
        else:
            ensure_model(db, target_ref)
            db._conn.execute(
                "UPDATE score_model SET score_current = score_current + ?, "
                "samples = samples + 1, updated_at = strftime('%s','now') "
                "WHERE model_ref = ?", (bonus, target_ref))
    return {"ok": True, "target": f"{target_kind}:{target_ref}", "bonus": bonus}


def adjust_score_experience(target_kind: str, target_ref: str, bonus: float = 0.0,
                            domaine: str = "general", niveau: str = "all",
                            reason: str = "") -> Dict[str, Any]:
    """Entrée publique : utilise le writer dédié du domaine score."""
    db = get_writer(WRITE_SCORE_TOKEN)
    try:
        return adjust_score(db, target_kind, target_ref, bonus, domaine,
                             niveau, reason)
    finally:
        db.close()


__all__ = [
    "upsert_model_bucket_counts",
    "upsert_score_batch",
    "upsert_score_batch_block",
    "upsert_score_benchmark_etire",
    "upsert_score_thinking_power",
    "adjust_score",
    "ensure_adress",
    "ensure_model",
]
