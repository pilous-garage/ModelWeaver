from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.batch import get_writer, WRITE_BATCH_TOKEN

TOKEN = WRITE_BATCH_TOKEN


def upsert_usage_history(
    table: str,
    bucket: int,
    provider_ref: str,
    model_ref: str,
    adresse_id: Optional[int],
    agent_id: Optional[str],
    requests: int,
    success_count: int,
    tokens_in: int,
    tokens_out: int,
    tokens_thinking: int,
    cost: float,
    first_call: Optional[int],
    last_call: Optional[int],
    req_total: int,
    tok_total: int,
) -> None:
    db = get_writer(TOKEN)
    with db.in_write():
        db.table(table).upsert({
            "bucket": bucket,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            "agent_id": agent_id or "",
            "requests": requests,
            "success_count": success_count,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "tokens_thinking": tokens_thinking,
            "cost": cost,
            "first_call": first_call,
            "last_call": last_call,
            "req_total": req_total,
            "tok_total": tok_total,
        }, conflict_cols=["bucket", "provider_ref", "model_ref", "agent_id"],
        token=TOKEN)


def open_model_sequence(
    provider_ref: str,
    model_ref: str,
    adresse_id: Optional[int],
    seq_type: str,
    seq_start: int,
    seq_end: int,
    requests: int,
    tokens_in: int,
    tokens_out: int,
    avg_latency_ms: float,
    error_code: str = "",
) -> int:
    db = get_writer(TOKEN)
    with db.in_write():
        return db.table("model_sequence").add({
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            "seq_type": seq_type,
            "seq_start": seq_start,
            "seq_end": seq_end,
            "requests": requests,
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "avg_latency_ms": avg_latency_ms,
            "error_code": error_code,
            "status": "open",
        }, token=TOKEN)


def extend_model_sequence(
    sequence_id: int,
    seq_end: int,
    requests: int,
    tokens_in: int,
    tokens_out: int,
    avg_latency_ms: float,
) -> None:
    db = get_writer(TOKEN)
    with db.in_write():
        db.table("model_sequence").update(
            {"id": sequence_id},
            {"seq_end": seq_end, "requests": requests,
             "tokens_in": tokens_in, "tokens_out": tokens_out,
             "avg_latency_ms": avg_latency_ms},
            token=TOKEN,
        )


def close_model_sequence(sequence_id: int) -> None:
    db = get_writer(TOKEN)
    with db.in_write():
        db.table("model_sequence").update(
            {"id": sequence_id}, {"status": "closed"}, token=TOKEN)


def open_caller_session(
    caller_id: str,
    provider_ref: Optional[str],
    model_ref: Optional[str],
    adresse_id: Optional[int],
    seq_start: int,
) -> int:
    db = get_writer(TOKEN)
    with db.in_write():
        return db.table("llm_caller_sessions").add({
            "caller_id": caller_id,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "adresse_id": adresse_id,
            "seq_start": seq_start,
            "seq_end": seq_start,
            "status": "open",
        }, token=TOKEN)


def close_caller_session(session_id: int, seq_end: int) -> None:
    db = get_writer(TOKEN)
    with db.in_write():
        db.table("llm_caller_sessions").update(
            {"id": session_id}, {"seq_end": seq_end, "status": "closed"},
            token=TOKEN)


def update_caller_session(session_id: int, requests: int, tin: int, tout: int,
                          avg_lat_ms: float) -> None:
    db = get_writer(TOKEN)
    with db.in_write():
        db.table("llm_caller_sessions").update(
            {"id": session_id},
            {"requests": requests, "tokens_in": tin, "tokens_out": tout,
             "avg_latency_ms": avg_lat_ms},
            token=TOKEN,
        )


def log_archive_processing(
    start_ts: int,
    end_ts: int,
    lines_read: int,
    warnings: str = "[]",
) -> int:
    db = get_writer(TOKEN)
    with db.in_write():
        return db.table("archive_processing_report").add({
            "start_ts": start_ts,
            "end_ts": end_ts,
            "lines_read": lines_read,
            "warnings": warnings,
        }, token=TOKEN)


# ── Colonnes par call_type (req_<type>/tok_<type>) ────────────────────────
def _ensure_rate_columns(table: str, ctypes=None) -> None:
    """Crée les colonnes req_<type>/tok_<type> si absentes (DDL locale batch)."""
    from modules.usage.rates import rate_fields
    try:
        cols = rate_fields(ctypes or [])
    except Exception:
        cols = []
    if not cols:
        return
    db = get_writer(TOKEN)
    cur = db._conn.execute(f"PRAGMA table_info({table})").fetchall()
    existing = {r[1] for r in cur}
    with db.in_write():
        for c in cols:
            if c not in existing:
                db._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {c} INTEGER DEFAULT 0")


def _safe_type(call_type: str) -> str:
    return (call_type or "chat").replace(" ", "_").replace("-", "_")


def batch_1m(rows, type_rows, resolve_adresse) -> int:
    """UPSERT cumulatif des agrégats 1-min dans usage_history_1m."""
    if not rows:
        return 0
    ctypes = sorted({r["call_type"] for r in type_rows if r.get("call_type")})
    _ensure_rate_columns("usage_history_1m", ctypes)
    from modules.usage.rates import rate_fields
    rate_cols = set(rate_fields(ctypes))

    type_idx: Dict[tuple, Dict[str, tuple]] = {}
    for tr in type_rows:
        key = (tr["bucket"], tr["provider_ref"], tr["model_ref"], tr["agent_id"])
        type_idx.setdefault(key, {})[_safe_type(tr["call_type"])] = (
            tr["req"], tr["tok"])

    db = get_writer(TOKEN)
    n = 0
    with db.in_write():
        for r in rows:
            aid = resolve_adresse(r["provider_ref"], r["model_ref"])
            req = r["requests"] or 0
            tin = r["tokens_in"] or 0
            tout = r["tokens_out"] or 0
            tok = tin + tout
            extra_cols, extra_vals = [], []
            for safe, (tr_req, tr_tok) in type_idx.get(
                (r["bucket"], r["provider_ref"], r["model_ref"], r["agent_id"]),
                {}).items():
                if f"req_{safe}" in rate_cols:
                    extra_cols += [f"req_{safe}", f"tok_{safe}"]
                    extra_vals += [tr_req or 0, tr_tok or 0]
            base_cols = ["bucket", "provider_ref", "model_ref", "adresse_id",
                         "agent_id", "requests", "success_count", "tokens_in",
                         "tokens_out", "tokens_thinking", "cost", "first_call",
                         "last_call", "req_total", "tok_total"]
            base_vals = [r["bucket"], r["provider_ref"], r["model_ref"], aid,
                         r["agent_id"] or "", req, r["success_count"] or 0, tin,
                         tout, r["tokens_thinking"] or 0, 0.0, r["first_call"],
                         r["last_call"], req, tok]
            all_cols = base_cols + extra_cols
            all_vals = base_vals + extra_vals
            col_sql = ", ".join(all_cols)
            ph = ", ".join("?" for _ in all_cols)
            upd = ", ".join(
                (f"{c} = {c} + excluded.{c}"
                 if c not in ("bucket", "provider_ref", "model_ref", "agent_id",
                              "adresse_id", "first_call", "last_call")
                 else (f"{c} = MIN({c}, excluded.{c})" if c == "first_call"
                       else f"{c} = MAX({c}, excluded.{c})"))
                for c in all_cols
            )
            db._conn.execute(
                f"INSERT INTO usage_history_1m ({col_sql}) VALUES ({ph}) "
                f"ON CONFLICT(bucket, provider_ref, model_ref, agent_id) "
                f"DO UPDATE SET {upd}", all_vals)
            n += 1
    return n


def cascade_level(src: str, dst: str, bucket_s: int, cutoff: int) -> int:
    """Agrège src→dst pour buckets figés, purge src."""
    db = get_writer(TOKEN)
    with db.in_write():
        db._conn.execute(f"""
            INSERT INTO {dst}
                (bucket, provider_ref, model_ref, adresse_id, agent_id,
                 requests, success_count, tokens_in, tokens_out,
                 tokens_thinking, cost, first_call, last_call)
            SELECT
                CAST(s.bucket / {bucket_s} AS INTEGER) * {bucket_s},
                COALESCE(s.provider_ref, ''), COALESCE(s.model_ref, ''),
                MAX(COALESCE(s.adresse_id, 0)), COALESCE(s.agent_id, ''),
                SUM(s.requests), SUM(s.success_count), SUM(s.tokens_in),
                SUM(s.tokens_out), SUM(s.tokens_thinking), SUM(s.cost),
                MIN(s.first_call), MAX(s.last_call)
            FROM {src} s
            WHERE s.bucket + {bucket_s} <= ?
            GROUP BY 1, 2, 3, 5
            ON CONFLICT(bucket, provider_ref, model_ref, agent_id) DO UPDATE SET
                requests = {dst}.requests + excluded.requests,
                success_count = {dst}.success_count + excluded.success_count,
                tokens_in = {dst}.tokens_in + excluded.tokens_in,
                tokens_out = {dst}.tokens_out + excluded.tokens_out,
                tokens_thinking = {dst}.tokens_thinking + excluded.tokens_thinking,
                cost = {dst}.cost + excluded.cost,
                last_call = MAX({dst}.last_call, excluded.last_call),
                first_call = MIN({dst}.first_call, excluded.first_call),
                adresse_id = excluded.adresse_id
        """, (cutoff,))
        cur = db._conn.execute(
            f"DELETE FROM {src} WHERE bucket + {bucket_s} <= ?", (cutoff,))
        return cur.rowcount if hasattr(cur, "rowcount") else 0


def purge_expired(now: int, keeps: Dict[str, int]) -> int:
    db = get_writer(TOKEN)
    n = 0
    with db.in_write():
        for tbl, keep in keeps.items():
            cur = db._conn.execute(
                f"DELETE FROM {tbl} WHERE bucket < ?", (now - keep,))
            n += cur.rowcount if hasattr(cur, "rowcount") else 0
    return n


def rebuild_caller_sessions(calls, resolve_adresse) -> int:
    """Reconstruit llm_caller_sessions depuis la liste d'appels."""
    db = get_writer(TOKEN)
    with db.in_write():
        db._conn.execute("DELETE FROM llm_caller_sessions")
    by_caller: Dict[str, list] = {}
    for c in calls:
        by_caller.setdefault(c["caller_id"], []).append(c)
    n = 0
    for cid, clist in by_caller.items():
        clist.sort(key=lambda c: c["created_at"])
        session = None
        for c in clist:
            if c["success"]:
                if session is None:
                    sid = open_caller_session(
                        cid, c["provider_ref"], c["model_ref"],
                        resolve_adresse(c["provider_ref"], c["model_ref"]),
                        c["created_at"])
                    session = {"id": sid, "requests": 1,
                               "tin": c["tokens_in"] or 0,
                               "tout": c["tokens_out"] or 0,
                               "lat": c["latency_ms"] or 0}
                    n += 1
                else:
                    session["requests"] += 1
                    session["tin"] += c["tokens_in"] or 0
                    session["tout"] += c["tokens_out"] or 0
                    session["lat"] += c["latency_ms"] or 0
                    update_caller_session(
                        session["id"], session["requests"], session["tin"],
                        session["tout"], session["lat"] / session["requests"])
            else:
                if session is not None:
                    close_caller_session(session["id"], c["created_at"])
                session = None
    return n


def reconcile_archive_upsert_max(rows, resolve_adresse) -> int:
    """UPSERT MAX des agrégats 1m depuis l'archive (ne réduit jamais)."""
    if not rows:
        return 0
    db = get_writer(TOKEN)
    n = 0
    with db.in_write():
        for r in rows:
            aid = resolve_adresse(r["provider_ref"], r["model_ref"])
            db._conn.execute("""
                INSERT INTO usage_history_1m
                    (bucket, provider_ref, model_ref, adresse_id, agent_id,
                     requests, tokens_in, tokens_out, tokens_thinking, cost,
                     first_call, last_call)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?)
                ON CONFLICT(bucket, provider_ref, model_ref, agent_id) DO UPDATE SET
                    requests = MAX(usage_history_1m.requests, excluded.requests),
                    tokens_in = MAX(usage_history_1m.tokens_in, excluded.tokens_in),
                    tokens_out = MAX(usage_history_1m.tokens_out, excluded.tokens_out),
                    tokens_thinking = MAX(usage_history_1m.tokens_thinking, excluded.tokens_thinking),
                    first_call = MIN(usage_history_1m.first_call, excluded.first_call),
                    last_call = MAX(usage_history_1m.last_call, excluded.last_call),
                    adresse_id = excluded.adresse_id
            """, (r["bucket"], r["provider_ref"], r["model_ref"], aid,
                  r["agent_id"] or "",
                  r["requests"] or 0, r["tokens_in"] or 0, r["tokens_out"] or 0,
                  r["tokens_thinking"] or 0, r["first_call"], r["last_call"]))
            n += 1
    return n


__all__ = [
    "upsert_usage_history",
    "open_model_sequence",
    "extend_model_sequence",
    "close_model_sequence",
    "open_caller_session",
    "close_caller_session",
    "update_caller_session",
    "log_archive_processing",
    "batch_1m",
    "cascade_level",
    "purge_expired",
    "rebuild_caller_sessions",
    "reconcile_archive_upsert_max",
]
