from __future__ import annotations

from typing import Any, Dict, List, Optional

from modules.sqlite.batch import db_ro


def get_usage_history(
    table: str,
    provider_ref: Optional[str] = None,
    model_ref: Optional[str] = None,
    bucket_start: Optional[int] = None,
    bucket_end: Optional[int] = None,
    limit: int = 1000,
) -> List[Dict[str, Any]]:
    db = db_ro()
    where: Dict[str, Any] = {}
    if provider_ref:
        where["provider_ref"] = provider_ref
    if model_ref:
        where["model_ref"] = model_ref
    rows = db.table(table).select(where=where, limit=limit)
    # Filtre bucket si nécessaire
    if bucket_start is not None or bucket_end is not None:
        filtered = []
        for r in rows:
            b = r.get("bucket")
            if bucket_start is not None and b < bucket_start:
                continue
            if bucket_end is not None and b > bucket_end:
                continue
            filtered.append(r)
        return filtered
    return rows


def get_model_sequences(
    provider_ref: str,
    model_ref: str,
    seq_type: Optional[str] = None,
    status: str = "closed",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    db = db_ro()
    where: Dict[str, Any] = {
        "provider_ref": provider_ref,
        "model_ref": model_ref,
        "status": status,
    }
    if seq_type:
        where["seq_type"] = seq_type
    return db.table("model_sequence").select(where=where, limit=limit, order_by="seq_start DESC")


def get_llm_caller_sessions(
    caller_id: Optional[str] = None,
    status: str = "open",
    limit: int = 100,
) -> List[Dict[str, Any]]:
    db = db_ro()
    where: Dict[str, Any] = {"status": status}
    if caller_id:
        where["caller_id"] = caller_id
    return db.table("llm_caller_sessions").select(where=where, limit=limit, order_by="seq_start DESC")


def get_archive_reports(limit: int = 100) -> List[Dict[str, Any]]:
    db = db_ro()
    return db.table("archive_processing_report").select(limit=limit, order_by="processed_at DESC")


def get_archive_report_count(start_ts: int, end_ts: int) -> int:
    """Nombre de rapports d'archive déjà traités pour [start_ts, end_ts]."""
    db = db_ro()
    row = db._conn.execute(
        "SELECT COUNT(*) c FROM archive_processing_report "
        "WHERE start_ts = ? AND end_ts = ?", (start_ts, end_ts)).fetchone()
    return int(row["c"]) if row else 0


__all__ = [
    "get_usage_history",
    "get_model_sequences",
    "get_llm_caller_sessions",
    "get_archive_reports",
    "get_archive_report_count",
]
