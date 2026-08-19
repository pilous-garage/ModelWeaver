from __future__ import annotations

"""
Writer dédié du domaine batch — le BATCHER (classe BatchWriter).

Service tick qui agrège runtime_llm.model_call_log vers batch.usage_history_*,
maintient batch.model_sequence (succès/échec) et batch.llm_caller_sessions,
purge TTL, et réconcilie depuis l'archive.

Tout le SQL est dans modules/sqlite/batch/write.py et
modules/sqlite/runtime_llm, ce module n'orchestre que (aucune requête SQL).
"""

import time
from typing import Any, Dict, List

from modules.sqlite.writer_dedie import WriterDedie


def _make_resolve_adresse() -> Any:
    """Callback de résolution (provider_ref, model_ref) → adresse_id.

    Socle = le quadruple (provider_ref, endpoint_ref, model_endpoint_ref,
    type_key) résolu par le domaine sqlite info_llm (lecture seule) —
    AUCUN import depuis l'extérieur du module sqlite. Cache par couple
    (provider, model), fallback 0 (adresse inconnue)."""
    cache: Dict[tuple, int] = {}

    def resolve(provider_ref: str, model_ref: str) -> int:
        key = (provider_ref, model_ref)
        if key in cache:
            return cache[key]
        if not provider_ref or not model_ref:
            cache[key] = 0
            return 0
        try:
            from modules.sqlite.info_llm import db_ro as info_db_ro
            from modules.sqlite.info_llm import read as info_read
            aid = info_read.resolve_adresse_id(
                info_db_ro(), provider_ref, model_ref)
        except Exception:
            aid = None
        cache[key] = aid if aid is not None else 0
        return cache[key]

    return resolve


_RESOLVE_ADRESSE = _make_resolve_adresse()


class BatchWriter(WriterDedie):
    DOMAIN = "batch"
    TOKEN = "write_batch"

    # Marge de sécurité : on ne batch jamais les appels de moins de 5 min.
    BATCH_MARGIN_SECONDS = 300

    # Cascade : (table_source, table_cible, taille_bucket_s, retention_s)
    CASCADE = [
        ("usage_history_1m", "usage_history_15m", 15 * 60, 24 * 3600),
        ("usage_history_15m", "usage_history_3h", 3 * 3600, 7 * 24 * 3600),
        ("usage_history_3h", "usage_history_1d", 24 * 3600, 30 * 24 * 3600),
        ("usage_history_1d", "usage_history_1w", 7 * 24 * 3600, 180 * 24 * 3600),
        ("usage_history_1w", "usage_history_1mo", 30 * 24 * 3600, 2 * 365 * 24 * 3600),
    ]

    KEEPS = {
        "usage_history_1m": 24 * 3600,
        "usage_history_15m": 7 * 24 * 3600,
        "usage_history_3h": 30 * 24 * 3600,
        "usage_history_1d": 180 * 24 * 3600,
        "usage_history_1w": 2 * 365 * 24 * 3600,
        "usage_history_1mo": 5 * 365 * 24 * 3600,
    }

    # ── helpers ────────────────────────────────────────────────────────────

    def _seq_open_or_extend(self, prov: str, model: str, ts: int, success: bool,
                            error_code: str = "", tin: int = 0, tout: int = 0,
                            lat: float = 0.0) -> None:
        """Ouvre/étend la séquence (success/failure) pour (provider, model)."""
        from modules.sqlite.batch import read as batch_read
        from modules.sqlite.batch import write as batch_write
        _typ = "success" if success else "failure"
        seqs = batch_read.get_model_sequences(
            provider_ref=prov, model_ref=model, status="open", limit=1)
        cur = seqs[0] if seqs else None
        if cur is not None and cur.get("seq_type") == _typ:
            req = (cur.get("requests") or 0) + 1
            sin = (cur.get("tokens_in") or 0) + (tin or 0)
            sout = (cur.get("tokens_out") or 0) + (tout or 0)
            prev = cur.get("avg_latency_ms") or 0.0
            avg = lat if req == 1 else (prev * (req - 1) + (lat or 0)) / req
            batch_write.extend_model_sequence(cur["id"], ts, req, sin, sout, avg)
            return
        if cur is not None:
            try:
                batch_write.close_model_sequence(cur["id"])
            except Exception:
                pass
        batch_write.open_model_sequence(
            provider_ref=prov, model_ref=model,
            adresse_id=_RESOLVE_ADRESSE(prov, model), seq_type=_typ,
            seq_start=ts, seq_end=ts, requests=1,
            tokens_in=tin or 0, tokens_out=tout or 0,
            avg_latency_ms=lat or 0.0, error_code=error_code or "")

    def _count_open(self, prov: str, model: str) -> int:
        from modules.sqlite.batch import read as batch_read
        return len(batch_read.get_model_sequences(
            provider_ref=prov, model_ref=model, status="open", limit=10000))

    def _rebuild_sequences(self, start_ts: int, end_ts: int) -> int:
        from modules.sqlite.runtime_llm import read as rt_read
        from modules.sqlite.batch import write as batch_write
        calls = (rt_read.list_archive_calls_in_frame(start_ts, end_ts)
                 + rt_read.list_detail_calls_in_frame(start_ts, end_ts))
        if not calls:
            return 0
        by_model: Dict[str, List[dict]] = {}
        for c in calls:
            by_model.setdefault(f"{c['provider_ref']}/{c['model_ref']}", []).append(c)
        n = 0
        for key, clist in by_model.items():
            prov, model = key.split("/", 1)
            clist.sort(key=lambda c: c["created_at"])
            for c in clist:
                before = self._count_open(prov, model)
                self._seq_open_or_extend(
                    prov, model, c["created_at"], bool(c["success"]),
                    error_code=c.get("error_code") or "",
                    tin=c["tokens_in"] or 0, tout=c["tokens_out"] or 0,
                    lat=c["latency_ms"] or 0)
                if self._count_open(prov, model) > before:
                    n += 1
        return n

    def _auto_reconcile(self) -> int:
        import json as _json
        from modules.sqlite.runtime_llm import read as rt_read
        from modules.sqlite.batch import read as batch_read
        from modules.sqlite.batch import write as batch_write
        now = int(time.time())
        n = 0
        for start, end in (
            ((now // 3600) * 3600 - 3600, (now // 3600) * 3600),       # heure précédente
            ((now // 86400) * 86400 - 86400, (now // 86400) * 86400),   # jour précédent
        ):
            try:
                done = batch_read.get_archive_report_count(start, end)
                if done:
                    continue
                arch = rt_read.list_archive_calls_in_frame(start, end)
                rows = self._aggregate_1m(
                    arch + rt_read.list_detail_calls_in_frame(start, end))
                ups = batch_write.reconcile_archive_upsert_max(
                    rows, _RESOLVE_ADRESSE)
                seq_n = self._rebuild_sequences(start, end)
                batch_write.log_archive_processing(
                    start, end, sum(r["requests"] or 0 for r in rows),
                    _json.dumps([]))
                n += ups + seq_n
            except Exception:
                continue
        return n

    # ── cycle ─────────────────────────────────────────────────────────────

    def run_once(self) -> Dict[str, Any]:
        from modules.sqlite.runtime_llm import read as rt_read, write as rt_write
        from modules.sqlite.batch import read as batch_read, write as batch_write

        frontier = rt_read.get_max_created_at()
        if frontier is None:
            return {"batched": 0, "cascade": 0, "purged": 0, "frontier": None}
        cutoff = frontier - self.BATCH_MARGIN_SECONDS

        # 1. Agrégation 1-min (SQL dans batch/write.py + runtime_llm/read.py).
        rows = rt_read.aggregate_call_log_1m(cutoff)
        type_rows = rt_read.aggregate_call_log_by_type(cutoff)
        batched = batch_write.batch_1m(rows, type_rows, _RESOLVE_ADRESSE)

        # 2. Séquences succès/échec par (provider, model).
        for c in rt_read.list_calls_for_sequences(cutoff):
            self._seq_open_or_extend(
                c["provider_ref"], c["model_ref"], c["created_at"], bool(c["success"]),
                error_code=c.get("error_code") or "",
                tin=c["tokens_in"] or 0, tout=c["tokens_out"] or 0,
                lat=c["latency_ms"] or 0)

        # 3. Archive + purge du détail (SQL dans runtime_llm/write.py).
        try:
            rt_write.archive_model_calls_up_to(cutoff)
        except Exception:
            pass

        # 4. Cascade vers les niveaux supérieurs.
        cascaded = 0
        for src, dst, bucket_s, _keep in self.CASCADE:
            cascaded += batch_write.cascade_level(src, dst, bucket_s, cutoff)

        # 5. Purge TTL.
        purged = batch_write.purge_expired(int(time.time()), self.KEEPS)

        # 6. Sessions par caller (rebuild depuis détail + archive).
        caller_sessions = 0
        try:
            caller_sessions = batch_write.rebuild_caller_sessions(
                rt_read.list_calls_for_sessions(), _RESOLVE_ADRESSE)
        except Exception:
            pass

        # 7. Réconciliation auto depuis l'archive (heure/veille).
        reconciled = 0
        try:
            reconciled = self._auto_reconcile()
        except Exception:
            pass

        # 8. Scoring (modules/usage) — conservé en try/except (non migré ici).
        score = {}
        try:
            from modules.usage.score_buckets import tick_buckets, update_score_batch
            tick_buckets(None, None, int(time.time()))
            score = {"buckets": update_score_batch(None, None)}
        except Exception:
            pass
        try:
            from modules.usage.score_benchmark import compute_etire
            score["benchmark"] = compute_etire(None, None)
        except Exception:
            pass

        return {"batched": batched, "cascade": cascaded, "purged": purged,
                "frontier": frontier, "caller_sessions": caller_sessions,
                "reconciled": reconciled, "score": score}

    def _aggregate_1m(self, calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Agrège une liste d'appels (dict) en buckets 1-min."""
        agg: Dict[tuple, Dict[str, Any]] = {}
        for c in calls:
            b = (int(c["created_at"]) // 60) * 60
            k = (b, c["provider_ref"], c["model_ref"], c.get("agent_id") or "")
            a = agg.setdefault(k, {"bucket": b, "provider_ref": c["provider_ref"],
                                   "model_ref": c["model_ref"],
                                   "agent_id": c.get("agent_id") or "",
                                   "requests": 0, "success_count": 0,
                                   "tokens_in": 0, "tokens_out": 0,
                                   "tokens_thinking": 0, "first_call": c["created_at"],
                                   "last_call": c["created_at"]})
            a["requests"] += 1
            a["success_count"] += 1 if c.get("success") else 0
            a["tokens_in"] += c.get("tokens_in") or 0
            a["tokens_out"] += c.get("tokens_out") or 0
            a["tokens_thinking"] += c.get("tokens_thinking") or 0
            a["first_call"] = min(a["first_call"], c["created_at"])
            a["last_call"] = max(a["last_call"], c["created_at"])
        return list(agg.values())

    def reconcile(self, start_ts: int, end_ts: int) -> Dict[str, Any]:
        """Réconciliation explicite d'un timeframe [start_ts, end_ts] depuis
        l'archive + le détail courant (utilisé par l'API usage/batch/run)."""
        import json as _json
        from modules.sqlite.runtime_llm import read as rt_read
        from modules.sqlite.batch import read as batch_read, write as batch_write
        done = batch_read.get_archive_report_count(start_ts, end_ts)
        if done:
            return {"skipped": True, "start": start_ts, "end": end_ts}
        arch = rt_read.list_archive_calls_in_frame(start_ts, end_ts)
        rows = self._aggregate_1m(
            arch + rt_read.list_detail_calls_in_frame(start_ts, end_ts))
        ups = batch_write.reconcile_archive_upsert_max(rows, _RESOLVE_ADRESSE)
        seq_n = self._rebuild_sequences(start_ts, end_ts)
        batch_write.log_archive_processing(
            start_ts, end_ts, sum(r["requests"] or 0 for r in rows), _json.dumps([]))
        return {"upserts": ups, "sequences": seq_n, "lines_read": sum(
            r["requests"] or 0 for r in rows)}


_writer: BatchWriter | None = None


def get_writer_dedie(batch: int = 1000) -> BatchWriter:
    global _writer
    if _writer is None:
        _writer = BatchWriter(batch=batch)
    return _writer


def tick() -> Dict[str, Any]:
    """Point d'entrée service_tick."""
    return get_writer_dedie().run_once()


def run_once() -> Dict[str, Any]:
    """Un cycle complet du batcher (compat API usage)."""
    return get_writer_dedie().run_once()


def reconcile(start_ts: int, end_ts: int) -> Dict[str, Any]:
    """Réconciliation explicite d'un timeframe (compat API usage)."""
    return get_writer_dedie().reconcile(start_ts, end_ts)


def main() -> None:
    get_writer_dedie().main(sleep_s=60.0)


__all__ = ["BatchWriter", "get_writer_dedie", "tick", "run_once",
           "reconcile", "main"]