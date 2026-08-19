from __future__ import annotations

from typing import Any, Dict, Optional

from modules.sqlite.runtime_llm import db


def log_model_call(
    adresse_id: Optional[int],
    provider_ref: str,
    model_ref: str,
    agent_id: Optional[str],
    caller_id: Optional[str],
    key_ref: Optional[str],
    tokens_in: int,
    tokens_out: int,
    tokens_thinking: int,
    cost: float,
    latency_ms: int,
    status: str,
    error_code: Optional[str],
    error_detail: Optional[str],
    sent_at: int,
    received_at: Optional[int],
    call_type: str = "chat",
    success: int = 1,
    error_msg: Optional[str] = None,
    provider_model_id: Optional[int] = None,
    meta_json: Optional[str] = None,
    task_id: Optional[int] = None,
    sub_task_id: Optional[int] = None,
    created_at: Optional[int] = None,
) -> int:
    db_conn = db()
    with db_conn.in_write():
        data = {
            "adresse_id": adresse_id,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "agent_id": agent_id,
            "caller_id": caller_id,
            "call_type": call_type,
            "success": success,
            "key_ref": key_ref,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_thinking": tokens_thinking,
            "cost": cost,
            "latency_ms": latency_ms,
            "status": status,
            "error_code": error_code,
            "error_detail": error_detail,
            "error_msg": error_msg,
            "provider_model_id": provider_model_id,
            "meta_json": meta_json,
            "task_id": task_id,
            "sub_task_id": sub_task_id,
            "sent_at": sent_at,
            "received_at": received_at,
        }
        if created_at is not None:
            data["created_at"] = created_at
        return db_conn.table("model_call_log").add(data)


def log_real_call(
    provider_ref: str,
    endpoint_id: Optional[int],
    key_ref: Optional[str],
    model_ref: str,
    adresse_id: Optional[int],
    agent_id: Optional[str],
    caller_id: Optional[str],
    sent_at: int,
    received_at: Optional[int],
    tokens_in: int,
    tokens_out: int,
    tokens_thinking: int,
    cost: float,
    status: str,
    error_code: Optional[str],
    error_detail: Optional[str],
    window_key: Optional[str],
) -> int:
    db_conn = db()
    with db_conn.in_write():
        return db_conn.table("real_call_models").add({
            "provider_ref": provider_ref,
            "endpoint_id": endpoint_id,
            "key_ref": key_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            "agent_id": agent_id,
            "caller_id": caller_id,
            "sent_at": sent_at,
            "received_at": received_at,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_thinking": tokens_thinking,
            "cost": cost,
            "status": status,
            "error_code": error_code,
            "error_detail": error_detail,
            "window_key": window_key,
        })


def update_adresse_runtime(
    adresse_id: int,
    available: Optional[int] = None,
    backoff_until: Optional[int] = None,
    error_since: Optional[int] = None,
    last_use: Optional[int] = None,
    last_respond: Optional[int] = None,
    error_count: Optional[int] = None,
) -> None:
    db_conn = db()
    with db_conn.in_write():
        changes: Dict[str, Any] = {}
        if available is not None:
            changes["available"] = available
        if backoff_until is not None:
            changes["backoff_until"] = backoff_until
        if error_since is not None:
            changes["error_since"] = error_since
        if last_use is not None:
            changes["last_use"] = last_use
        if last_respond is not None:
            changes["last_respond"] = last_respond
        if error_count is not None:
            changes["error_count"] = error_count
        if changes:
            db_conn.table("adresse_runtime").update(
                {"adresse_id": adresse_id}, changes)


def update_budget_final(
    budget_ref: str,
    used_cost: Optional[float] = None,
    used_tokens: Optional[int] = None,
) -> None:
    db_conn = db()
    with db_conn.in_write():
        changes: Dict[str, Any] = {}
        if used_cost is not None:
            changes["used_cost"] = used_cost
        if used_tokens is not None:
            changes["used_tokens"] = used_tokens
        if changes:
            db_conn.table("budget_final").update(
                {"budget_ref": budget_ref}, changes)


def upsert_model_efficacy(
    model_ref: str,
    provider_ref: Optional[str],
    use_case: str,
    score_quality: float,
    score_speed: float,
    score_cost: float,
    score_reliability: float,
    samples: int,
) -> None:
    db_conn = db()
    with db_conn.in_write():
        db_conn.table("model_efficacy").upsert({
            "model_ref": model_ref,
            "provider_ref": provider_ref,
            "use_case": use_case,
            "score_quality": score_quality,
            "score_speed": score_speed,
            "score_cost": score_cost,
            "score_reliability": score_reliability,
            "samples": samples,
        }, conflict_cols=["model_ref", "provider_ref", "use_case"])


def delete_model_call_log_up_to(cutoff: int) -> int:
    db_conn = db()
    with db_conn.in_write():
        cur = db_conn._conn.execute(
            "DELETE FROM model_call_log WHERE created_at <= ?", (cutoff,))
        return cur.rowcount if hasattr(cur, "rowcount") else 0


def archive_model_calls_up_to(cutoff: int) -> int:
    db_conn = db()
    with db_conn.in_write():
        rows = db_conn._conn.execute(
            "SELECT * FROM model_call_log WHERE created_at <= ?", (cutoff,)).fetchall()
        count = 0
        for r in rows:
            r = dict(r)
            db_conn.table("model_call_log_archive").add({
                "adresse_id": r.get("adresse_id"),
                "provider_ref": r.get("provider_ref"),
                "model_ref": r.get("model_ref"),
                "agent_id": r.get("agent_id"),
                "caller_id": r.get("caller_id"),
                "call_type": r.get("call_type"),
                "success": r.get("success"),
                "key_ref": r.get("key_ref"),
                "tokens_in": r.get("tokens_in"),
                "tokens_out": r.get("tokens_out"),
                "tokens_thinking": r.get("tokens_thinking"),
                "cost": r.get("cost"),
                "latency_ms": r.get("latency_ms"),
                "status": r.get("status"),
                "error_code": r.get("error_code"),
                "error_detail": r.get("error_detail"),
                "error_msg": r.get("error_msg"),
                "provider_model_id": r.get("provider_model_id"),
                "meta_json": r.get("meta_json"),
                "task_id": r.get("task_id"),
                "sub_task_id": r.get("sub_task_id"),
                "sent_at": r.get("sent_at"),
                "received_at": r.get("received_at"),
                "created_at": r.get("created_at"),
            })
            count += 1
        db_conn._conn.execute(
            "DELETE FROM model_call_log WHERE created_at <= ?", (cutoff,))
    return count


__all__ = [
    "log_model_call",
    "log_real_call",
    "update_adresse_runtime",
    "update_budget_final",
    "upsert_model_efficacy",
    "delete_model_call_log_up_to",
    "archive_model_calls_up_to",
]
