"""score_buckets — buckets stables de compteurs (succ/tot) par modèle.

Refonte du scoring de fail (V0.16). On stocke des COMPTEURS nb_success /
nb_fail par bucket (jamais des scores) dans `model_bucket_counts`, rotation par
tête (`score_bucket_heads`). Les scores de zone sont calculés à la demande par
somme, avec une racine carrée (^1/2) sur CHAQUE score de zone (adoucit la
pénalité sur les succès rates faibles) — JAMAIS sur le score final :

  f(succ, tot)       = (1 + succ) / (1 + tot)
  score_durée        = sqrt( f(succ_durée, tot_durée) )        # chacun ∈ [0,1]
  score_last_5       = sqrt( f(succ_5min, tot_5min) )          # log direct
  score_1h           = sqrt( f(Σ 12×5m) )
  score_1d           = sqrt( f(Σ 12×5m + Σ 23×1h) )            # cumulatif
  score_1w           = sqrt( f(Σ 12×5m + Σ 23×1h + Σ 6×1d) )
  score_all          = sqrt( f(all_time_succ, all_time_tot) )
  score_total (0–1)  = (score_last_5 + score_1h + score_1d
                        + score_1w + score_all) / 5

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
    """{key: (nb_success, nb_total)} des appels [lo, hi).

    Sources :
      - usage_history_1m (batchs 1 min agrégés, avec success_count) : la
        source PERSISTANTE — le batcher y agrège le détail puis le purge de
        model_call_log. Sans elle, les appels batchés disparaissent du scoring.
      - model_call_log (détail récent non encore batché) : complément.

    key = '{provider_ref}/{model_ref}'. Les appels sans model_ref résolu sont
    ignorés (clé vide)."""
    out: Dict[str, Tuple[int, int]] = {}
    # 1) Batchs 1 min persistants dans la fenêtre (source fiable).
    try:
        rows = rt.conn.execute("""
            SELECT provider_ref, model_ref, requests, success_count
            FROM usage_history_1m
            WHERE bucket >= ? AND bucket < ? AND provider_ref != '' AND model_ref != ''
        """, (lo, hi)).fetchall()
        for pr, mr, tot, succ in rows:
            if not pr or not mr:
                continue
            key = f"{pr}/{mr}"
            a, b = out.get(key, (0, 0))
            out[key] = (a + int(succ or 0), b + int(tot or 0))
    except Exception:
        pass
    # 2) Détail récent non encore batché (fenêtre pas figée).
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
        for pr, mr, tot, succ in rows:
            if not pr or not mr:
                continue
            key = f"{pr}/{mr}"
            a, b = out.get(key, (0, 0))
            out[key] = (a + int(succ or 0), b + int(tot or 0))
    except Exception:
        pass
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

def _update_scores_sql(rt, cat=None, now: Optional[int] = None) -> None:
    """Calcule les 5 scores de zone (last_5/1h/1d/1w/all, chacun 0-1) + score_total
    (0-1 = moyenne des 5) pour TOUS les modèles, en UNE requête. Fenêtres
    cumulatives (la plus longue inclut les plus courtes) :
       last_5 = 5 dernières minutes (log direct)
       1h = Σ 12×5m ; 1d = Σ 12×5m + Σ 23×1h ; 1w = 1d + Σ 6×1d ; all = cumul.
    Chaque score_durée = sqrt((1+succ)/(1+tot))."""
    s5s = "+".join(f"s5_succ_{i}" for i in range(12))
    s5t = "+".join(f"s5_tot_{i}" for i in range(12))
    shs = "+".join(f"sh_succ_{i}" for i in range(23))
    sht = "+".join(f"sh_tot_{i}" for i in range(23))
    sds = "+".join(f"sd_succ_{i}" for i in range(6))
    sdt = "+".join(f"sd_tot_{i}" for i in range(6))
    # score_last_5 d'abord : log direct des 5 dernières minutes, par modèle.
    if cat is not None:
        last5 = score_last_5(rt, cat, now)
        for key, v in last5.items():
            pr, mr = key.split("/", 1)
            rt.conn.execute(
                "UPDATE model_bucket_counts SET score_last_5 = ? "
                "WHERE provider_ref = ? AND model_ref = ?",
                (min(1.0, max(0.0, v)), pr, mr))
        rt.conn.commit()
    rt.conn.execute(f"""
        UPDATE model_bucket_counts SET
            score_1h = sqrt((1.0 + ({s5s})) / (1.0 + ({s5t}))),
            score_1d = sqrt((1.0 + ({s5s}) + ({shs})) / (1.0 + ({s5t}) + ({sht}))),
            score_1w = sqrt((1.0 + ({s5s}) + ({shs}) + ({sds}))
                            / (1.0 + ({s5t}) + ({sht}) + ({sdt}))),
            score_all = sqrt((1.0 + all_time_succ) / (1.0 + all_time_tot)),
            score_total = (score_last_5
                         + sqrt((1.0 + ({s5s})) / (1.0 + ({s5t})))
                         + sqrt((1.0 + ({s5s}) + ({shs})) / (1.0 + ({s5t}) + ({sht})))
                         + sqrt((1.0 + ({s5s}) + ({shs}) + ({sds}))
                                / (1.0 + ({s5t}) + ({sht}) + ({sdt})))
                         + sqrt((1.0 + all_time_succ) / (1.0 + all_time_tot)))
                        / 5.0,
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
    # Si jamais initialisé (purge, redémarrage) : on backfill les buckets 5m
    # FIGÉS passés (jusqu'à la fenêtre 1h = 12 slots). Sans ça, start=now-300
    # → hi=now → jamais figé → tick_buckets ne ferme RIEN pour toujours.
    if not h5["timestamp_start"]:
        h5["timestamp_start"] = ((now - 600) // 300) * 300
    start = h5["timestamp_start"]
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
            # 23 buckets 1h révolus → rollup 1d. Le bucket 1d le plus ancien
            # (sd_0) sort de la rotation → il est AJOUTÉ au compteur all_time.
            rt.conn.execute(
                "UPDATE model_bucket_counts SET "
                "all_time_succ = all_time_succ + sd_succ_0, "
                "all_time_tot  = all_time_tot  + sd_tot_0")
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
    _update_scores_sql(rt, cat, now)
    return 1


# ── score à la demande ───────────────────────────────────────────────────

def score_last_5(rt, cat, now: Optional[int] = None) -> Dict[str, float]:
    """{key: sqrt(f(succ,tot))} sur les 5 dernières minutes.

    Sources : usage_history_1m (batchs figés) + model_call_log (détail frais).
    """
    now = now or int(time.time())
    cnt = _counts_from_log(rt, cat, now - 300, now)
    out: Dict[str, float] = {}
    for key, (succ, tot) in cnt.items():
        out[key] = math.sqrt((1.0 + succ) / (1.0 + tot))
    return out


def _counts_from_log(rt, cat, lo: int, hi: int) -> Dict[str, Tuple[int, int]]:
    out: Dict[str, Tuple[int, int]] = {}
    # batchs 1 min persistants dans la fenêtre
    try:
        rows = rt.conn.execute("""
            SELECT provider_ref, model_ref, requests, success_count
            FROM usage_history_1m
            WHERE bucket >= ? AND bucket < ? AND provider_ref != '' AND model_ref != ''
        """, (lo, hi)).fetchall()
        for pr, mr, tot, succ in rows:
            if not pr or not mr:
                continue
            key = f"{pr}/{mr}"
            a, b = out.get(key, (0, 0))
            out[key] = (a + int(succ or 0), b + int(tot or 0))
    except Exception:
        pass
    # détail récent (fenêtre pas encore batchée)
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
        for pr, mr, tot, succ in rows:
            if not pr or not mr:
                continue
            key = f"{pr}/{mr}"
            a, b = out.get(key, (0, 0))
            out[key] = (a + int(succ or 0), b + int(tot or 0))
    except Exception:
        pass
    return out


def score_succes_for(rt, key: str) -> float:
    """Score de SUCCÈS global d'un modèle = moyenne des 5 scores de zone.

    score_total (0–1) = (score_last_5 + score_1h + score_1d + score_1w +
    score_all) / 5, chaque zone = sqrt((1+succ)/(1+tot)) — 1.0 = meilleur
    score (aucun échec).
    Modèle SANS AUCUNE ligne (jamais testé) → neutre 0.5 (ni parfait, ni
    éliminé). Une dérive transitoire succ>tot ne fait jamais dépasser 1.0."""
    row = rt.conn.execute(
        "SELECT score_total FROM model_bucket_counts WHERE provider_ref = ? "
        "AND model_ref = ?", (key.split("/", 1)[0], key.split("/", 1)[1])
    ).fetchone()
    if row is None:
        return 0.5  # jamais testé → neutre
    total = float(row["score_total"])
    return min(1.0, max(0.0, total))


def update_score_batch(rt, cat, now: Optional[int] = None) -> int:
    """Recompose score_batch (succès/latence/etire/final + 5 scores de zone)
    depuis les buckets stables. Retourne le nombre de modèles mis à jour."""
    now = now or int(time.time())
    # latence moyenne (score actuel) depuis le détail des 5 dernières minutes
    lat = _latency_by_model(cat, now)
    bench = {r["model_ref"]: r["score_etire"]
             for r in rt.conn.execute(
                 "SELECT model_ref, score_etire FROM score_benchmark_etire")}
    rows = rt.conn.execute("""
        SELECT provider_ref, model_ref, score_total, score_last_5,
               score_1h, score_1d, score_1w, score_all
        FROM model_bucket_counts
    """).fetchall()
    upserts = 0
    for r in rows:
        key = f"{r['provider_ref']}/{r['model_ref']}"
        succes = score_succes_for(rt, key)
        score_lat = lat.get(key, 1.0)
        etire = bench.get(r["model_ref"], 0.1)
        final = etire * score_lat * succes
        # Résout l'adresse_id (répertoire provider_model_address) pour que
        # score_batch soit référencé par adresse (pas par textes).
        aid = _resolve_adresse(cat, r["provider_ref"], r["model_ref"])
        rt.conn.execute("""
            INSERT OR REPLACE INTO score_batch
                (provider_ref, model_ref, adresse_id, score_fail_rate,
                 score_latency, score_etire, score_final,
                 score_last_5, score_1h, score_1d, score_1w, score_all,
                 updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%s','now'))
        """, (r["provider_ref"], r["model_ref"], aid, succes, score_lat,
              etire, final,
              r["score_last_5"], r["score_1h"], r["score_1d"],
              r["score_1w"], r["score_all"]))
        upserts += 1
    rt.conn.commit()
    return upserts


def _resolve_adresse(cat, provider_ref: str, model_ref: str):
    """Résout (provider_ref, model_ref) → adresse_id du répertoire, sinon 0."""
    try:
        from services.llm_allocation.address import resolve_address
        aid = resolve_address(provider_ref, model_ref, cat)
        return aid if aid is not None else 0
    except Exception:
        return 0


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


def reset_buckets(rt, cat, now: Optional[int] = None) -> int:
    """Reconstruit les buckets stables depuis model_call_log + archive.

    Répartit les appels passés (log + model_call_log_archive) par modèle dans
    les fenêtres non chevauchantes : 12×5m (dernière heure), 23×1h (23h avant),
    6×1d (6 jours avant), all_time (avant 7 jours). Les 5 dernières minutes ne
    sont PAS stockées (score_last_5 à la demande). Réinitialise les têtes.
    Retourne le nombre de modèles reconstruits."""
    now = now or int(time.time())
    rt.conn.execute("DELETE FROM model_bucket_counts")
    # compteurs par (provider, model, bucket 5m) sur les 7 derniers jours
    rows = cat.conn.execute("""
        SELECT COALESCE(p.ref, ''), COALESCE(m.ref, ''),
               CAST(l.created_at / 300 AS INTEGER) * 300 AS b5,
               COUNT(*),
               SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END)
        FROM (
            SELECT provider_id, model_id, success, created_at
            FROM model_call_log
            UNION ALL
            SELECT provider_id, model_id, success, created_at
            FROM model_call_log_archive
        ) l
        LEFT JOIN catalogue_providers p ON p.id = l.provider_id
        LEFT JOIN catalogue_models m ON m.id = l.model_id
        WHERE l.created_at >= ? AND l.created_at < ?
        GROUP BY 1, 2, 3
    """, (now - 7 * 86400, now)).fetchall()
    # all_time : avant 7 jours (cumul par modèle)
    allt = cat.conn.execute("""
        SELECT COALESCE(p.ref, ''), COALESCE(m.ref, ''),
               COUNT(*),
               SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END)
        FROM (
            SELECT provider_id, model_id, success, created_at
            FROM model_call_log
            UNION ALL
            SELECT provider_id, model_id, success, created_at
            FROM model_call_log_archive
        ) l
        LEFT JOIN catalogue_providers p ON p.id = l.provider_id
        LEFT JOIN catalogue_models m ON m.id = l.model_id
        WHERE l.created_at < ?
        GROUP BY 1, 2
    """, (now - 7 * 86400,)).fetchall()

    models: Dict[str, Dict[str, int]] = {}

    def _acc(key, btype, idx, succ, tot):
        d = models.setdefault(key, {})
        d[f"{btype}_succ_{idx}"] = d.get(f"{btype}_succ_{idx}", 0) + succ
        d[f"{btype}_tot_{idx}"] = d.get(f"{btype}_tot_{idx}", 0) + tot

    for pr, mr, b5, tot, succ in rows:
        if not pr or not mr:
            continue
        key = f"{pr}/{mr}"
        succ = int(succ or 0); tot = int(tot or 0)
        age = now - int(b5)
        if age >= 7 * 86400:
            models.setdefault(key, {})
            models[key]["all_time_succ"] = models[key].get("all_time_succ", 0) + succ
            models[key]["all_time_tot"] = models[key].get("all_time_tot", 0) + tot
        elif age >= 86400:
            idx = 6 - int(age / 86400)
            if 0 <= idx < 6:
                _acc(key, "sd", idx, succ, tot)
        elif age >= 3600:
            idx = 23 - int(age / 3600)
            if 0 <= idx < 23:
                _acc(key, "sh", idx, succ, tot)
        elif age >= 300:
            idx = 11 - int(age / 300)
            if 0 <= idx < 12:
                _acc(key, "s5", idx, succ, tot)
        # age < 300 : les 5 dernières minutes → score_last_5 (à la demande)

    for pr, mr, tot, succ in allt:
        if not pr or not mr:
            continue
        key = f"{pr}/{mr}"
        d = models.setdefault(key, {})
        d["all_time_succ"] = d.get("all_time_succ", 0) + int(succ or 0)
        d["all_time_tot"] = d.get("all_time_tot", 0) + int(tot or 0)

    # ── Batchs agrégés persistants (usage_history_1m + niveaux supérieurs) ──
    # Les appels batchés (le batcher purge model_call_log vers usage_history_1m
    # puis cascade 15m/3h/1d/1w) ne sont plus dans le détail. On les réintègre
    # par batch (requests, success_count). Les batchs sont non chevauchants
    # entre niveaux : 1m (détail récent) + 15m + 3h + 1d couvrent 7 jours.
    try:
        _UH_TABLES = ("usage_history_1m", "usage_history_15m",
                      "usage_history_3h", "usage_history_1d")
        for _t in _UH_TABLES:
            uh_rows = rt.conn.execute(f"""
                SELECT provider_ref, model_ref, bucket, requests, success_count
                FROM {_t}
                WHERE provider_ref != '' AND model_ref != '' AND bucket >= ?
            """, (now - 7 * 86400,)).fetchall()
            for pr, mr, b1, tot, succ in uh_rows:
                if not pr or not mr:
                    continue
                key = f"{pr}/{mr}"
                succ = int(succ or 0); tot = int(tot or 0)
                # bucket 5m [b5, b5+300) qui contient ce batch
                b5 = (int(b1) // 300) * 300
                age = now - b5
                if age >= 7 * 86400:
                    models.setdefault(key, {})
                    models[key]["all_time_succ"] = models[key].get("all_time_succ", 0) + succ
                    models[key]["all_time_tot"] = models[key].get("all_time_tot", 0) + tot
                elif age >= 86400:
                    idx = 6 - int(age / 86400)
                    if 0 <= idx < 6:
                        _acc(key, "sd", idx, succ, tot)
                elif age >= 3600:
                    idx = 23 - int(age / 3600)
                    if 0 <= idx < 23:
                        _acc(key, "sh", idx, succ, tot)
                elif age >= 300:
                    idx = 11 - int(age / 300)
                    if 0 <= idx < 12:
                        _acc(key, "s5", idx, succ, tot)
                # age < 300 : 5 dernières min → score_last_5 (à la demande)
    except Exception:
        pass

    n = 0
    for key, d in models.items():
        pr, mr = key.split("/", 1)
        aid = _resolve_adresse(cat, pr, mr)
        cols = ", ".join(list(d.keys())
                         + ["provider_ref", "model_ref", "adresse_id", "updated_at"])
        marks = ", ".join(["?"] * (len(d) + 4))
        rt.conn.execute(
            f"INSERT OR REPLACE INTO model_bucket_counts ({cols}) "
            f"VALUES ({marks})",
            list(d.values()) + [pr, mr, aid, now])
        n += 1
    # réinitialiser les têtes (le reset repart du présent)
    for btype, qt, size in (("5m", 12, 300), ("1h", 23, 3600), ("1d", 6, 86400)):
        rt.conn.execute("""
            INSERT OR REPLACE INTO score_bucket_heads
                (bucket_type, last_bucket, timestamp_start, bucket_nb, bucket_qt, bucket_size)
            VALUES (?, ?, ?, 0, ?, ?)
        """, (btype, now, now, qt, size))
    rt.conn.commit()
    _update_scores_sql(rt, cat, now)
    # Purge score_batch : les lignes sans correspondance dans les buckets
    # (variantes non résolues, refs obsolètes) sont retirées. Le prochain
    # update_score_batch recompose le reste.
    rt.conn.execute("""
        DELETE FROM score_batch WHERE rowid NOT IN (
            SELECT s.rowid FROM score_batch s
            JOIN model_bucket_counts b
              ON b.provider_ref = s.provider_ref AND b.model_ref = s.model_ref
        )
    """)
    rt.conn.commit()
    return n
