"""score_blocks — blocs glissants de fail_rate / latence par (provider, model).

Principe (data-driven, cohérent avec usage_batcher) :
  - Le détail (model_call_log) est agrégé en blocs de 5 min stockés dans
    score_batch_blocks_5m. Un bloc n'est calculé qu'UNE fois, quand sa fenêtre
    est figée (bucket + 300 <= cutoff), et le tracking meta
    (score_batch.last_5m) empêche de le recalculer.
  - Les blocs supérieurs rollupent les blocs inférieurs :
        blocks_5m (300s) → blocks_1h (3600s) → blocks_1d (86400s)
    chaque niveau n'étant calculé que pour les buckets figés non encore
    rollupés (meta score_batch.last_1h / last_1d).
  - score_batch : par (provider, model), les 4 fenêtres sont composées en
    sommant les TOTAUX des blocs stockés (requests, fail_count,
    total_latency_ms), puis divisées. Pas de rescan du détail à chaque tick.
        fr_5m = dernier bloc 5m              (fail_count / requests)
        fr_1h = 12 derniers blocs 5m         (somme fail / somme requests)
        fr_1j = 24 derniers blocs 1h         (somme fail / somme requests)
        fr_1w = 7 derniers blocs 1d          (somme fail / somme requests)
    fail_rate = 0 si aucun appel (pas d'échec → score parfait). Latence =
    moyenne pondérée (total_latency_ms / requests).

Appelé depuis usage_batcher.run_once AVANT _batch_1m (qui supprime le détail),
pour que les blocs 5m soient calculés pendant que les lignes existent encore.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.usage.usage_batcher import BATCH_MARGIN_SECONDS  # noqa: E402

BLOCK_5M = 300
BLOCK_1H = 3600
BLOCK_1D = 86400

# Pondération du score fail_rate composite (récent dominant, somme = 1).
FAIL_WEIGHTS = (0.4, 0.3, 0.2, 0.1)

_META_5M = "score_batch.last_5m"
_META_1H = "score_batch.last_1h"
_META_1D = "score_batch.last_1d"


def _last_frozen_bucket(now: int, block_s: int) -> int:
    """Le bucket <block_s> le plus récent dont la fenêtre est <= now (figée)."""
    b = ((now - block_s) // block_s) * block_s
    return b if b >= 0 else 0


def _get_meta(rt, key: str) -> int:
    try:
        row = rt.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return int(row["value"]) if row else 0
    except Exception:
        return 0


def _set_meta(rt, key: str, value: int):
    rt.conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value))


def compute_block_5m(cat, rt, cutoff: int) -> int:
    """Calcule les blocs 5m figés non encore traités depuis model_call_log.

    Rattrapage : on agrège en UNE requête (GROUP BY bucket) tous les buckets 5m
    figés entre last_5m et le dernier figé, borné à 24h en arrière si on part
    de zéro (évite un scan total au premier démarrage).

    Retourne le nombre de lignes insérées (0 si rien de nouveau).
    """
    b5 = _last_frozen_bucket(cutoff, BLOCK_5M)
    if b5 == 0:
        return 0
    last = _get_meta(rt, _META_5M)
    if b5 <= last:
        return 0
    lo = max(last, b5 - 24 * 3600)
    try:
        rows = cat.conn.execute("""
            SELECT CAST(l.created_at / ? AS INTEGER) * ? AS bucket,
                   COALESCE(p.ref, '') AS provider_ref,
                   COALESCE(m.ref, '') AS model_ref,
                   COUNT(*) AS requests,
                   SUM(CASE WHEN l.success = 0 AND COALESCE(l.error_code, '')
                                 NOT IN ('rate_limit', 'quota') THEN 1 ELSE 0 END)
                       AS fail_count,
                   SUM(l.latency_ms) AS total_latency_ms
            FROM model_call_log l
            LEFT JOIN catalogue_providers p ON p.id = l.provider_id
            LEFT JOIN catalogue_models m ON m.id = l.model_id
            WHERE l.created_at >= ? AND l.created_at < ?
            GROUP BY 1, 2, 3
        """, (BLOCK_5M, BLOCK_5M, lo, b5 + BLOCK_5M)).fetchall()
        for r in rows:
            rt.conn.execute("""
                INSERT OR REPLACE INTO score_batch_blocks_5m
                    (bucket, provider_ref, model_ref, requests, fail_count,
                     total_latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["bucket"], r["provider_ref"], r["model_ref"], r["requests"] or 0,
                  r["fail_count"] or 0, r["total_latency_ms"] or 0))
        _set_meta(rt, _META_5M, b5)
        rt.conn.commit()
        return len(rows)
    except Exception:
        try:
            rt.conn.rollback()
        except Exception:
            pass
        return 0


def _rollup(rt, cutoff: int, src: str, dst: str, block_s: int,
            meta_key: str) -> int:
    """Agrège les blocs src figés non encore rollupés vers dst (rattrapage).

    Tous les buckets dst figés entre last et le dernier figé sont agrégés en
    une seule requête GROUP BY. Retourne le nombre de lignes insérées.
    """
    b_dst = _last_frozen_bucket(cutoff, block_s)
    if b_dst == 0:
        return 0
    last = _get_meta(rt, meta_key)
    if b_dst <= last:
        return 0
    try:
        rows = rt.conn.execute(f"""
            SELECT CAST(bucket / ? AS INTEGER) * ? AS bucket,
                   provider_ref, model_ref,
                   SUM(requests) AS requests,
                   SUM(fail_count) AS fail_count,
                   SUM(total_latency_ms) AS total_latency_ms
            FROM {src}
            WHERE bucket > ? AND bucket < ? + ?
            GROUP BY 1, 2, 3
        """, (block_s, block_s, last, b_dst, block_s)).fetchall()
        for r in rows:
            rt.conn.execute(f"""
                INSERT OR REPLACE INTO {dst}
                    (bucket, provider_ref, model_ref, requests, fail_count,
                     total_latency_ms)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["bucket"], r["provider_ref"], r["model_ref"], r["requests"] or 0,
                  r["fail_count"] or 0, r["total_latency_ms"] or 0))
        _set_meta(rt, meta_key, b_dst)
        rt.conn.commit()
        return len(rows)
    except Exception:
        try:
            rt.conn.rollback()
        except Exception:
            pass
        return 0


def rollup_1h(rt, cutoff: int) -> int:
    """blocks_5m (12 par heure) → blocks_1h pour les heures figées non rollupées."""
    return _rollup(rt, cutoff, "score_batch_blocks_5m", "score_batch_blocks_1h",
                   BLOCK_1H, _META_1H)


def rollup_1d(rt, cutoff: int) -> int:
    """blocks_1h (24 par jour) → blocks_1d pour les jours figés non rollupés."""
    return _rollup(rt, cutoff, "score_batch_blocks_1h", "score_batch_blocks_1d",
                   BLOCK_1D, _META_1D)


def _win_rows(rt, table: str, n: int) -> List[dict]:
    """Les <n> derniers blocs par (provider, model), totaux sommés par modèle."""
    return rt.conn.execute(f"""
        SELECT provider_ref, model_ref,
               SUM(requests) AS requests, SUM(fail_count) AS fail_count,
               SUM(total_latency_ms) AS total_latency_ms
        FROM (
            SELECT b.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY provider_ref, model_ref
                       ORDER BY bucket DESC) AS rn
            FROM {table} b
        )
        WHERE rn <= ?
        GROUP BY provider_ref, model_ref
    """, (n,)).fetchall()


def update_scores(rt, latence_penalise: Optional[float] = None,
                  latence_regule: Optional[float] = None) -> int:
    """Recompose score_batch (4 fenêtres) depuis les blocs stockés, tout modèle.

    score_final = score_etire × score_latence × (1 - score_fail_rate)
      - score_etire : benchmark étiré (score_benchmark_etire) croisé par model_ref,
        baseline 0.1 si absent (modèle jamais benchmarké).
      - score_latence : décroissance exponentielle de la latence moyenne
        (référence absolue) :
            score_latence = exp( -(max(penalise, lat_s) - penalise)/regule )
        lat_s = latence moyenne en secondes. Sous `penalise` → 1.0 (parfait) ;
        1min1s (61s) avec défauts (1s/60s) → e⁻¹ ≈ 0.37.
        Paramètres configurables (défauts : config score.latence_penalise /
        score.latence_regule, eux-mêmes 1s/60s). Une tâche tolérante peut
        passer penalise élevé + regule élevé pour que les LLM lents restent
        compétitifs.
      - score_fail_rate : composite pondéré (0.4/0.3/0.2/0.1), déjà dans 0-1.

    Retourne le nombre de lignes score_batch mises à jour.
    """
    import math
    if latence_penalise is None or latence_regule is None:
        try:
            from modules.config.config_manager import config
            latence_penalise = float(config.get("score.latence_penalise", 1.0))
            latence_regule = float(config.get("score.latence_regule", 60.0))
        except Exception:
            latence_penalise = 1.0
            latence_regule = 60.0
    try:
        rows5 = {f"{r['provider_ref']}/{r['model_ref']}": dict(r)
                 for r in _win_rows(rt, "score_batch_blocks_5m", 1)}
        rows1h = {f"{r['provider_ref']}/{r['model_ref']}": dict(r)
                  for r in _win_rows(rt, "score_batch_blocks_5m", 12)}
        rows1j = {f"{r['provider_ref']}/{r['model_ref']}": dict(r)
                  for r in _win_rows(rt, "score_batch_blocks_1h", 24)}
        rows1w = {f"{r['provider_ref']}/{r['model_ref']}": dict(r)
                  for r in _win_rows(rt, "score_batch_blocks_1d", 7)}
        keys = set(rows5) | set(rows1h) | set(rows1j) | set(rows1w)
        if not keys:
            return 0

        # Benchmarks étirés par model_ref (cross-référence).
        bench = {r["model_ref"]: r["score_etire"]
                 for r in rt.conn.execute(
                     "SELECT model_ref, score_etire FROM score_benchmark_etire")}

        upserts = 0
        for key in keys:
            prov, model = key.split("/", 1)
            r5 = rows5.get(key, {})
            rh = rows1h.get(key, {})
            rj = rows1j.get(key, {})
            rw = rows1w.get(key, {})

            def fr_and_lat(r):
                req = r.get("requests") or 0
                if req == 0:
                    return 0, 0, 0, 0
                fail = r.get("fail_count") or 0
                lat = r.get("total_latency_ms") or 0
                return req, fail, lat, fail / req

            r5_ = fr_and_lat(r5)
            rh_ = fr_and_lat(rh)
            rj_ = fr_and_lat(rj)
            rw_ = fr_and_lat(rw)
            frs = [r5_[3], rh_[3], rj_[3], rw_[3]]
            score_fail_rate = sum(w * f for w, f in zip(FAIL_WEIGHTS, frs))
            # Latence moyenne pondérée sur les appels (récent dominant), ms.
            lat_w = (r5_[2], rh_[2], rj_[2], rw_[2])
            req_w = (r5_[0], rh_[0], rj_[0], rw_[0])
            tot_lat = sum(a for a in lat_w)
            tot_req = sum(a for a in req_w)
            lat_ms = (tot_lat / tot_req) if tot_req else 0.0
            # Score latence : exp( -(max(penalise, lat_s) - penalise)/regule ),
            # lat_s en secondes. Sous penalise → 1.0 (parfait) ; 1min1s (61s)
            # avec défauts (1s/60s) → e⁻¹ ≈ 0.37.
            if tot_req == 0:
                score_latence = 1.0
            else:
                lat_s = max(latence_penalise, lat_ms / 1000.0)
                score_latence = math.exp(-(lat_s - latence_penalise) / latence_regule)
            score_etire = bench.get(model, 0.1)
            score_final = score_etire * score_latence * (1.0 - score_fail_rate)
            rt.conn.execute("""
                INSERT OR REPLACE INTO score_batch
                    (provider_ref, model_ref,
                     requests_5m, requests_1h, requests_1j, requests_1w,
                     fail_count_5m, fail_count_1h, fail_count_1j, fail_count_1w,
                     total_lat_ms_5m, total_lat_ms_1h, total_lat_ms_1j, total_lat_ms_1w,
                     fr_5m, fr_1h, fr_1j, fr_1w,
                     lat_5m_ms, lat_1h_ms, lat_1j_ms, lat_1w_ms,
                     score_fail_rate, score_latency, score_etire, score_final,
                     updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
            """, (prov, model,
                  r5_[0], rh_[0], rj_[0], rw_[0],
                  r5_[1], rh_[1], rj_[1], rw_[1],
                  r5_[2], rh_[2], rj_[2], rw_[2],
                  r5_[3], rh_[3], rj_[3], rw_[3],
                  r5_[2] / r5_[0] if r5_[0] else 0.0,
                  rh_[2] / rh_[0] if rh_[0] else 0.0,
                  rj_[2] / rj_[0] if rj_[0] else 0.0,
                  rw_[2] / rw_[0] if rw_[0] else 0.0,
                  score_fail_rate, score_latence,
                  score_etire, score_final))
            upserts += 1
        rt.conn.commit()
        return upserts
    except Exception:
        try:
            rt.conn.rollback()
        except Exception:
            pass
        return 0


def run(cat, rt, frontier: Optional[int]) -> Dict[str, Any]:
    """Un cycle de calcul des blocs + scores. Appeler AVANT _batch_1m."""
    if frontier is None:
        return {"blocks_5m": 0, "blocks_1h": 0, "blocks_1d": 0, "scores": 0}
    cutoff = frontier - BATCH_MARGIN_SECONDS
    b5 = compute_block_5m(cat, rt, cutoff)
    b1h = rollup_1h(rt, cutoff)
    b1d = rollup_1d(rt, cutoff)
    scores = update_scores(rt) if (b5 or b1h or b1d) else 0
    return {"blocks_5m": b5, "blocks_1h": b1h, "blocks_1d": b1d, "scores": scores}


def main() -> None:
    from modules.sql.db import CatalogueDB, RuntimeDB
    cat = CatalogueDB()
    rt = RuntimeDB()
    frontier = int(time.time())
    print(f"run manuel (frontier={frontier}) →", run(cat, rt, frontier))


if __name__ == "__main__":
    main()
