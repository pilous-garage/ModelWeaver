from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.runtime_llm import db_ro


def get_adresse_runtime(adresse_id: int) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("adresse_runtime").select(where={"adresse_id": adresse_id})
    return rows[0] if rows else None


def list_model_call_log(
    model_ref: Optional[str] = None,
    provider_ref: Optional[str] = None,
    limit: int = 1000,
    since: Optional[int] = None,
    until: Optional[int] = None,
) -> List[Dict[str, Any]]:
    db = db_ro()
    q = "SELECT * FROM model_call_log WHERE 1=1"
    args: List[Any] = []
    if model_ref:
        q += " AND model_ref = ?"
        args.append(model_ref)
    if provider_ref:
        q += " AND provider_ref = ?"
        args.append(provider_ref)
    if since is not None:
        q += " AND created_at >= ?"
        args.append(since)
    if until is not None:
        q += " AND created_at <= ?"
        args.append(until)
    q += " ORDER BY sent_at DESC LIMIT ?"
    args.append(limit)
    rows = db._conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def get_max_created_at() -> Optional[int]:
    try:
        db = db_ro()
        row = db._conn.execute(
            "SELECT MAX(created_at) as m FROM model_call_log").fetchone()
        return int(row["m"]) if row and row["m"] else None
    except Exception:
        return None


def list_model_call_log(limit: int = 1000, since: Optional[int] = None,
                        until: Optional[int] = None,
                        call_type: Optional[str] = None) -> List[Dict[str, Any]]:
    db = db_ro()
    where: Dict[str, Any] = {}
    if since is not None:
        where["created_at"] = (">=", since)
    if until is not None:
        where["created_at"] = ("<=", until)
    if call_type is not None:
        where["call_type"] = call_type
    return db.table("model_call_log").select(where=where, limit=limit,
                                              order_by="created_at")


def get_call_types_until(cutoff: int) -> List[str]:
    db = db_ro()
    rows = db._conn.execute(
        "SELECT DISTINCT call_type FROM model_call_log WHERE created_at <= ?",
        (cutoff,)).fetchall()
    return [r["call_type"] for r in rows if r["call_type"]]


def list_archive_in_frame(start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    db = db_ro()
    rows = db._conn.execute(
        "SELECT * FROM model_call_log_archive "
        "WHERE created_at >= ? AND created_at < ? ORDER BY created_at",
        (start_ts, end_ts)).fetchall()
    return [dict(r) for r in rows]


def list_real_call_models(limit: int = 1000, since: Optional[int] = None,
                          until: Optional[int] = None) -> List[Dict[str, Any]]:
    db = db_ro()
    q = "SELECT * FROM real_call_models WHERE 1=1"
    args: List[Any] = []
    if since is not None:
        q += " AND created_at >= ?"
        args.append(since)
    if until is not None:
        q += " AND created_at <= ?"
        args.append(until)
    q += " ORDER BY sent_at LIMIT ?"
    args.append(limit)
    rows = db._conn.execute(q, args).fetchall()
    return [dict(r) for r in rows]


def aggregate_call_log_1m(cutoff: int) -> List[Dict[str, Any]]:
    """Agrégat 1-min des appels <= cutoff (pour usage_history_1m)."""
    db = db_ro()
    rows = db._conn.execute("""
        SELECT
            CAST(created_at / 60 AS INTEGER) * 60 AS bucket,
            COALESCE(provider_ref, '') AS provider_ref,
            COALESCE(model_ref, '') AS model_ref,
            COALESCE(agent_id, '') AS agent_id,
            COUNT(*) AS requests,
            SUM(success) AS success_count,
            SUM(tokens_in) AS tokens_in,
            SUM(tokens_out) AS tokens_out,
            SUM(tokens_thinking) AS tokens_thinking,
            MIN(created_at) AS first_call,
            MAX(created_at) AS last_call
        FROM model_call_log
        WHERE created_at <= ?
        GROUP BY 1, 2, 3, 4
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def aggregate_call_log_by_type(cutoff: int) -> List[Dict[str, Any]]:
    """Agrégat 1-min par call_type (pour colonnes req_<type>/tok_<type>)."""
    db = db_ro()
    rows = db._conn.execute("""
        SELECT CAST(created_at / 60 AS INTEGER) * 60 AS bucket,
               COALESCE(provider_ref, '') AS provider_ref,
               COALESCE(model_ref, '') AS model_ref,
               COALESCE(agent_id, '') AS agent_id,
               COALESCE(call_type, 'chat') AS call_type,
               COUNT(*) AS req,
               SUM(tokens_in + tokens_out) AS tok
        FROM model_call_log
        WHERE created_at <= ?
        GROUP BY 1, 2, 3, 4, 5
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def list_calls_for_sequences(cutoff: int) -> List[Dict[str, Any]]:
    """Appels <= cutoff ordonnés (pour model_sequence)."""
    db = db_ro()
    rows = db._conn.execute("""
        SELECT COALESCE(provider_ref, '?') AS provider_ref,
               COALESCE(model_ref, '?') AS model_ref,
               success, created_at, latency_ms,
               tokens_in, tokens_out, error_code
        FROM model_call_log
        WHERE created_at <= ?
        ORDER BY created_at
    """, (cutoff,)).fetchall()
    return [dict(r) for r in rows]


def list_calls_for_sessions() -> List[Dict[str, Any]]:
    """Tous les appels (détail + archive) ordonnés par caller_id."""
    db = db_ro()
    out = []
    for tbl in ("model_call_log", "model_call_log_archive"):
        rows = db._conn.execute(f"""
            SELECT caller_id, COALESCE(provider_ref, '?') provider_ref,
                   COALESCE(model_ref, '?') model_ref, success, created_at,
                   tokens_in, tokens_out, latency_ms
            FROM {tbl}
            WHERE caller_id IS NOT NULL
        """).fetchall()
        out.extend([dict(r) for r in rows])
    return out


def list_archive_calls_in_frame(start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    db = db_ro()
    rows = db._conn.execute("""
        SELECT COALESCE(provider_ref, '?') provider_ref,
               COALESCE(model_ref, '?') model_ref,
               success, created_at, tokens_in, tokens_out, latency_ms, error_code
        FROM model_call_log_archive
        WHERE created_at >= ? AND created_at < ?
    """, (start_ts, end_ts)).fetchall()
    return [dict(r) for r in rows]


def list_detail_calls_in_frame(start_ts: int, end_ts: int) -> List[Dict[str, Any]]:
    db = db_ro()
    rows = db._conn.execute("""
        SELECT COALESCE(provider_ref, '?') provider_ref,
               COALESCE(model_ref, '?') model_ref,
               success, created_at, tokens_in, tokens_out, latency_ms, error_code
        FROM model_call_log
        WHERE created_at >= ? AND created_at < ?
    """, (start_ts, end_ts)).fetchall()
    return [dict(r) for r in rows]


def get_budget_final(budget_ref: str) -> Optional[Dict[str, Any]]:
    db = db_ro()
    rows = db.table("budget_final").select(where={"budget_ref": budget_ref})
    return rows[0] if rows else None


def get_model_efficacy(model_ref: str, provider_ref: Optional[str] = None) -> List[Dict[str, Any]]:
    db = db_ro()
    where: Dict[str, Any] = {"model_ref": model_ref}
    if provider_ref:
        where["provider_ref"] = provider_ref
    return db.table("model_efficacy").select(where=where)


__all__ = [
    "get_adresse_runtime",
    "list_model_call_log",
    "get_max_created_at",
    "get_call_types_until",
    "list_archive_in_frame",
    "list_real_call_models",
    "aggregate_call_log_1m",
    "aggregate_call_log_by_type",
    "list_calls_for_sequences",
    "list_calls_for_sessions",
    "list_archive_calls_in_frame",
    "list_detail_calls_in_frame",
    "get_budget_final",
    "get_model_efficacy",
]
