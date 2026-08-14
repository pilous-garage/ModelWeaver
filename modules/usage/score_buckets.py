"""score_buckets — buckets stables de compteurs (succ/tot) par modèle.

Refonte du scoring de fail (V0.16). On stocke des COMPTEURS nb_success /
nb_fail par bucket (jamais des scores) dans `model_bucket_counts`, rotation par
tête (`score_bucket_heads`). Les scores de zone sont calculés à la demande par
somme, avec une racine carrée (^1/2) sur CHAQUE score de zone (adoucit la
pénalité sur les succès rates faibles) — JAMAIS sur le score final :

  f(succ, tot)       = (1 + succ) / (1 + tot)
  score_1h           = sqrt( f(Σ 12×5m) )
  score_1d           = sqrt( f(Σ 12×5m + Σ 23×1h) )     # 1h exclue de la 1d
  score_1w           = sqrt( f(Σ 12×5m + Σ 23×1h + Σ 6×1d) )
  score_total (0–3)  = score_1h + score_1d + score_1w
  score_fail         = ( sqrt(score_last_5) + score_total ) / 4

La latence garde son score actuel (exp de la latence moyenne) — la refonte
latence par méthode (chat : tok/lat ; streaming : ttft + tok/s) viendra plus
tard (carnet).

Le batcheur est appelé depuis usage_batcher.run_once AVANT le purge du détail.
"""

from __future__ import annotations

import math
import time
from typing import Dict, List, Optional, Tuple

from modules.usage.usage_batcher import BATCH_MARGIN_SECONDS

# (nb slots, préfixe, taille en s)
SLOTS = {
    "5m": (12, "s5", 300),
    "1h": (23, "sh", 3600),
    "1d": (6, "sd", 86400),
}


# ── têtes (rotation) ─────────────────────────────────────────────────────

def _head(rt, btype: str) -> Dict[str, Any]:
    row = rt.conn.execute(
        "SELECT * FROM score_bucket_heads WHERE bucket_type = ?", (btype,)
    ).fetchone()
    if row:
        return dict(row)
    n, _pfx, size = SLOTS[btype]
    return {"bucket_type": btype, "last_bucket": 0, "timestamp_start": 0,
            "bucket_nb": 0, "bucket_qt": n, "bucket_size": size}


def _set_head(rt, h: Dict[str, Any]) -> None:
    rt.conn.execute("""
        INSERT OR REPLACE INTO score_bucket_heads
            (bucket_type, last_bucket, timestamp_start, bucket_nb,
             bucket_qt, bucket_size)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (h["bucket_type"], h["last_bucket"], h["timestamp_start"],
          h["bucket_nb"], h["bucket_qt"], h["bucket_size"]))


# ── compteurs depuis le log ──────────────────────────────────────────────

def _counts(rt, cat, lo: int, hi: int) -> Dict[str, Tuple[int, int]]:
    """{key: (nb_success, nb_total)} des appels [lo, hi) de model_call_log.

    key = '{provider_ref}/{model_ref}'. Les appels sans model_ref résolu
    (model_id=0) sont ignorés (clé vide)."""
    try:
        rows = cat.conn.execute("""
            SELECT COALESCE(p.ref, ''), COALESCE(m.ref, ''),
                   COUNT(*),
                   SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END)
            FROM model_call_log l
            LEFT JOIN catalogue_providers p ON p.id = l.provider_id
            LEFT JOIN catalogue_models m ON m.id = l.model_id
            WHERE l.created_at >= ? AND l.created_at < ?
            GROUP BY 1, 2
        """, (lo, hi)).fetchall()
    except Exception:
        return {}
    out: Dict[str, Tuple[int, int]] = {}
    for pr, mr, tot, succ in rows:
        if not pr or not mr:
            continue
        key = f"{pr}/{mr}"
        out[key] = (int(succ or 0), int(tot or 0))
    return out


# ── rotation des slots ───────────────────────────────────────────────────

def _shift(rt, btype: str) -> None:
    """Décale les slots du niveau (le plus ancien tombe), dernière colonne libre."""
    n, pfx, _s = SLOTS[btype]
    for i in range(n - 1):
        rt.conn.execute(f"UPDATE model_bucket_counts SET "
                        f"{pfx}_succ_{i} = {pfx}_succ_{i + 1}, "
                        f"{pfx}_tot_{i} = {pfx}_tot_{i + 1}")


def _set_slot(rt, key: str, btype: str, succ: int, tot: int) -> None:
    pr, mr = key.split("/", 1)
    n, pfx, _s = SLOTS[btype]
    last = n - 1
    rt.conn.execute(f"""
        INSERT INTO model_bucket_counts ({pfx}_succ_{last}, {pfx}_tot_{last},
                                         provider_ref, model_ref, updated_at)
        VALUES (?, ?, ?, ?, strftime('%s','now'))
        ON CONFLICT(provider_ref, model_ref) DO UPDATE SET
            {pfx}_succ_{last} = excluded.{pfx}_succ_{last},
            {pfx}_tot_{last}  = excluded.{pfx}_tot_{last},
            updated_at = strftime('%s','now')
    """, (succ, tot, pr, mr))


def _rollup_sum(rt, btype: str, nb: int) -> None:
    """Somme des `nb` slots du niveau → dernier slot (après décalage)."""
    n, pfx, _s = SLOTS[btype]
    succ = "+".join(f"{pfx}_succ_{i}" for i in range(n))
    tot = "+".join(f"{pfx}_tot_{i}" for i in range(n))
    rt.conn.execute(f"""
        UPDATE model_bucket_counts SET
            {pfx}_succ_{n - 1} = ({succ}),
            {pfx}_tot_{n - 1}  = ({tot}),
            updated_at = strftime('%s','now')
    """)


# ── mise à jour SQL globale (avec sqrt par zone) ─────────────────────────

def _update_scores_sql(rt) -> None:
    """Calcule score_1h/1d/1w (sqrt) + score_total (0–3) pour TOUS les modèles,
    en UNE requête. Fenêtres non chevauchantes :
      1h = Σ 12×5m ; 1d = Σ 12×5m + Σ 23×1h ; 1w = 1d + Σ 6×1d."""
    s5s = "+".join(f"s5_succ_{i}" for i in range(12))
    s5t = "+".join(f"s5_tot_{i}" for i in range(12))
    shs = "+".join(f"sh_succ_{i}" for i in range(23))
    sht = "+".join(f"sh_tot_{i}" for i in range(23))
    sds = "+".join(f"sd_succ_{i}" for i in range(6))
    sdt = "+".join(f"sd_tot_{i}" for i in range(6))
    rt.conn.execute(f"""
        UPDATE model_bucket_counts SET
            score_1h = sqrt((1.0 + ({s5s})) / (1.0 + ({s5t}))),
            score_1d = sqrt((1.0 + ({s5s}) + ({shs})) / (1.0 + ({s5t}) + ({sht}))),
            score_1w = sqrt((1.0 + ({s5s}) + ({shs}) + ({sds}))
                            / (1.0 + ({s5t}) + ({sht}) + ({sdt}))),
            score_total = sqrt((1.0 + ({s5s})) / (1.0 + ({s5t})))
                        + sqrt((1.0 + ({s5s}) + ({shs})) / (1.0 + ({s5t}) + ({sht})))
                        + sqrt((1.0 + ({s5s}) + ({shs}) + ({sds}))
                               / (1.0 + ({s5t}) + ({sht}) + ({sdt}))),
            updated_at = strftime('%s','now')
    """)
    rt.conn.commit()


# ── tick du batcheur ─────────────────────────────────────────────────────

def tick_buckets(rt, cat, now: Optional[int] = None) -> int:
    """Une passe de rotation : clôture le bucket 5m écoulé, rollup 1h/1d quand
    les têtes atteignent leur seuil, puis mise à jour SQL globale des scores.

    Retourne 1 si un bucket 5m a été écrit, 0 sinon (rien de figé)."""
    now = now or int(time.time())
    h5 = _head(rt, "5m")
    start = h5["timestamp_start"] or now - 300
    b5 = (start // 300) * 300
    lo, hi = b5, b5 + 300
    if now - hi < BATCH_MARGIN_SECONDS:
        return 0  # période pas encore figée
    rows = _counts(rt, cat, lo, hi)
    _shift(rt, "5m")
    for key, (succ, tot) in rows.items():
        _set_slot(rt, key, "5m", succ, tot)
    h5["timestamp_start"] = hi
    h5["last_bucket"] = hi
    h5["bucket_nb"] += 1

    if h5["bucket_nb"] >= h5["bucket_qt"]:
        # 12 buckets 5m révolus → rollup 1h (somme des 12 slots 5m)
        _shift(rt, "1h")
        _rollup_sum(rt, "5m", 12)
        h5["bucket_nb"] = 0
        _set_head(rt, h5)
        h1 = _head(rt, "1h")
        h1["bucket_nb"] += 1
        h1["last_bucket"] = hi
        if h1["bucket_nb"] >= h1["bucket_qt"]:
            # 23 buckets 1h révolus → rollup 1d
            _shift(rt, "1d")
            _rollup_sum(rt, "1h", 23)
            h1["bucket_nb"] = 0
            _set_head(rt, h1)
            hd = _head(rt, "1d")
            hd["bucket_nb"] += 1
            hd["last_bucket"] = hi
            _set_head(rt, hd)
        else:
            _set_head(rt, h1)
    else:
        _set_head(rt, h5)
    _update_scores_sql(rt)
    return 1


# ── score à la demande ───────────────────────────────────────────────────

def score_last_5(cat, now: Optional[int] = None) -> Dict[str, float]:
    """{key: sqrt(f(succ,tot))} sur les 5 dernières minutes (log direct)."""
    now = now or int(time.time())
    cnt = _counts_from_log(cat, now - 300, now)
    out: Dict[str, float] = {}
    for key, (succ, tot) in cnt.items():
        out[key] = math.sqrt((1.0 + succ) / (1.0 + tot))
    return out


def _counts_from_log(cat, lo: int, hi: int) -> Dict[str, Tuple[int, int]]:
    try:
        rows = cat.conn.execute("""
            SELECT COALESCE(p.ref, ''), COALESCE(m.ref, ''),
                   COUNT(*),
                   SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END)
            FROM model_call_log l
            LEFT JOIN catalogue_providers p ON p.id = l.provider_id
            LEFT JOIN catalogue_models m ON m.id = l.model_id
            WHERE l.created_at >= ? AND l.created_at < ?
            GROUP BY 1, 2
        """, (lo, hi)).fetchall()
    except Exception:
        return {}
    out: Dict[str, Tuple[int, int]] = {}
    for pr, mr, tot, succ in rows:
        if not pr or not mr:
            continue
        out[f"{pr}/{mr}"] = (int(succ or 0), int(tot or 0))
    return out


def score_succes_for(rt, key: str, last5: Optional[float] = None) -> float:
    """Score de SUCCÈS global d'un modèle = (sqrt(score_last_5) + score_total)/4.

    score_total (0–3) = score_1h + score_1d + score_1w, chaque zone =
    sqrt((1+succ)/(1+tot)) — 1.0 = meilleur score (aucun échec / jamais testé)."""
    row = rt.conn.execute(
        "SELECT score_total FROM model_bucket_counts WHERE provider_ref = ? "
        "AND model_ref = ?", (key.split("/", 1)[0], key.split("/", 1)[1])
    ).fetchone()
    total = float(row["score_total"]) if row else 3.0
    if last5 is None:
        last5 = 1.0  # aucune donnée récente → neutre (jamais testé)
    return (last5 + total) / 4.0


def update_score_batch(rt, cat, now: Optional[int] = None) -> int:
    """Recompose score_batch (succès/latence/etire/final) depuis les buckets
    stables + score_last_5 du log. Retourne le nombre de modèles mis à jour."""
    now = now or int(time.time())
    last5 = score_last_5(cat, now)
    # latence moyenne (score actuel) depuis le détail des 5 dernières minutes
    lat = _latency_by_model(cat, now)
    bench = {r["model_ref"]: r["score_etire"]
             for r in rt.conn.execute(
                 "SELECT model_ref, score_etire FROM score_benchmark_etire")}
    rows = rt.conn.execute(
        "SELECT provider_ref, model_ref, score_total FROM model_bucket_counts"
    ).fetchall()
    upserts = 0
    for r in rows:
        key = f"{r['provider_ref']}/{r['model_ref']}"
        succes = score_succes_for(rt, key, last5.get(key, 1.0))
        score_lat = lat.get(key, 1.0)
        etire = bench.get(r["model_ref"], 0.1)
        final = etire * score_lat * succes
        rt.conn.execute("""
            INSERT OR REPLACE INTO score_batch
                (provider_ref, model_ref, score_fail_rate, score_latency,
                 score_etire, score_final, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, strftime('%s','now'))
        """, (r["provider_ref"], r["model_ref"], succes, score_lat, etire, final))
        upserts += 1
    rt.conn.commit()
    return upserts


def _latency_by_model(cat, now: int) -> Dict[str, float]:
    """{key: score_latence} = exp(-(max(1, lat_s)-1)/60) sur les 5 dernières min."""
    try:
        rows = cat.conn.execute("""
            SELECT COALESCE(p.ref, ''), COALESCE(m.ref, ''),
                   AVG(l.latency_ms)
            FROM model_call_log l
            LEFT JOIN catalogue_providers p ON p.id = l.provider_id
            LEFT JOIN catalogue_models m ON m.id = l.model_id
            WHERE l.created_at >= ? AND l.created_at < ?
              AND l.success = 1
            GROUP BY 1, 2
        """, (now - 300, now)).fetchall()
    except Exception:
        return {}
    out: Dict[str, float] = {}
    for pr, mr, lat_ms in rows:
        if not pr or not mr or lat_ms is None:
            continue
        lat_s = max(1.0, (float(lat_ms) / 1000.0))
        out[f"{pr}/{mr}"] = math.exp(-(lat_s - 1.0) / 60.0)
    return out
