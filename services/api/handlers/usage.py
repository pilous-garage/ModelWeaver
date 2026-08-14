from services.api._shared import _quiet
from services.api.router import register

# Granularité (taille de bucket en secondes) de chaque table de cascade.
_CASCADE_BUCKETS = {
    "usage_history_1m": 60,
    "usage_history_15m": 15 * 60,
    "usage_history_3h": 3 * 3600,
    "usage_history_1d": 24 * 3600,
    "usage_history_1w": 7 * 24 * 3600,
    "usage_history_1mo": 30 * 24 * 3600,
}


def _bucket_size(tbl: str) -> int:
    return _CASCADE_BUCKETS.get(tbl, 3600)


# Fenêtres supportées (nom → secondes) : courtes (5m/15m), moyennes (1h/4h),
# longues (24h/7j). Chaque fenêtre a une granularité de POINT de série
# (taille des buckets de sortie pour le graphe).
WINDOWS = {
    "5m": (300, 60), "15m": (900, 60),
    "1h": (3600, 300), "4h": (14400, 900),
    "24h": (86400, 3600), "7j": (604800, 10800),
}


def _window_seconds(name: str) -> int:
    return WINDOWS.get(name, (3600, 300))[0]


def _series_granularity(name: str) -> int:
    return WINDOWS.get(name, (3600, 300))[1]

# ── Usage ───────────────────────────────────────────────────────────────

def op_usage_budget(params):
    from services.usage import get_budget
    return get_budget(params)


def op_usage_free_tier(params):
    from services.usage import get_free_tier
    return get_free_tier(params)


# ── Moniteur LLM (consommation 1h/24h/7j) ──────────────────────────────

def op_usage_monitor(params):
    """Consommation LLM agrégée par fenêtres 1h/24h/7j, par provider/modèle.

    Sources : usage_history_1m (1h), usage_history_1h (24h/7j). Si ces
    historiques sont vides (collecteur d'usage pas encore alimenté), on
    aggrège directement depuis model_call_log (appels LLM réels, source de
    vérité) sur la même fenêtre temporelle.
    Retourne pour chaque fenêtre un résumé global + la répartition par
    provider et par modèle (requests, tokens in/out/thinking, cost).
    """
    import time as _t
    from services.api._shared import _get_rt, _get_cat
    rt = _get_rt()
    now = int(_t.time())
    out = {}
    cat = _get_cat()
    for wname, wsec in WINDOWS.items():
        wsec = _window_seconds(wname)
        rows = None
        # Le batchage en cascade pousse les données vers les tables plus
        # grossières : pour couvrir une fenêtre, il faut UNION les tables dont
        # la granularité est <= la fenêtre (ex. 1h = 1m + 15m ; 24h = 1m+15m+3h).
        # Ordre de granularité (fine → grosse).
        CASCADE_TABLES = ("usage_history_1m", "usage_history_15m",
                          "usage_history_3h", "usage_history_1d",
                          "usage_history_1w", "usage_history_1mo")
        # Granularité minimale suffisante pour la fenêtre (ne pas inclure les
        # tables plus grossières que la fenêtre elle-même).
        relevant = [t for t in CASCADE_TABLES
                    if _bucket_size(t) <= wsec]
        # Agrégats des tables de cascade (runtime DB).
        cascade_rows = []
        if relevant:
            try:
                unions = []
                for tbl in relevant:
                    unions.append(f"""
                        SELECT provider_ref, model_ref, agent_id,
                               requests, tokens_in, tokens_out,
                               tokens_thinking, cost
                        FROM {tbl} WHERE bucket >= {now - wsec}""")
                sql = ("SELECT provider_ref, model_ref, agent_id, "
                       "SUM(requests) req, SUM(tokens_in) tin, "
                       "SUM(tokens_out) tout, SUM(tokens_thinking) tthink, "
                       "SUM(cost) cost FROM ( " + " UNION ALL ".join(unions) +
                       " ) GROUP BY provider_ref, model_ref, agent_id "
                       "ORDER BY cost DESC")
                cascade_rows = rt.conn.execute(sql).fetchall()
            except Exception:
                cascade_rows = []
        # DéTAIL RÉCENT non batché (catalogue DB) : model_call_log contient les
        # appels de moins de ~5 min (le batcheur ne les a pas encore agrégés).
        # Sans cette source, les fenêtres courtes (5m/15m) sous-comptent
        # (15m < 5m bizarre) car elles ne voient que ce qui est batché.
        detail_rows = []
        if cat is not None:
            try:
                detail_rows = cat.conn.execute("""
                    SELECT COALESCE(p.ref, '?') AS provider_ref,
                           COALESCE(m.ref, pm.provider_model_name, '?') AS model_ref,
                           COALESCE(l.agent_id, '') AS agent_id,
                           COUNT(*) AS req, SUM(l.tokens_in) AS tin,
                           SUM(l.tokens_out) AS tout,
                           SUM(l.tokens_thinking) AS tthink, 0.0 AS cost
                    FROM model_call_log l
                    LEFT JOIN catalogue_providers p ON p.id = l.provider_id
                    LEFT JOIN catalogue_models m ON m.id = l.model_id
                    LEFT JOIN provider_models pm ON pm.id = l.provider_model_id
                    WHERE l.created_at >= ?
                    GROUP BY p.ref, m.ref, pm.provider_model_name, l.agent_id
                """, (now - wsec,)).fetchall()
            except Exception:
                detail_rows = []
        # Fusion cascade + détail (mêmes colonnes).
        rows = [dict(r) for r in cascade_rows] + [dict(r) for r in detail_rows]
        # Si aucun agrégat dispo → détail model_call_log (source de vérité).
        if not rows and cat is not None:
            try:
                rows = cat.conn.execute("""
                    SELECT p.ref AS provider_ref,
                           COALESCE(m.ref, pm.provider_model_name, '?') AS model_ref,
                           l.agent_id,
                           COUNT(*) AS req,
                           SUM(l.tokens_in) AS tin,
                           SUM(l.tokens_out) AS tout,
                           SUM(l.tokens_thinking) AS tthink,
                           0.0 AS cost
                    FROM model_call_log l
                    LEFT JOIN catalogue_providers p ON p.id = l.provider_id
                    LEFT JOIN catalogue_models m ON m.id = l.model_id
                    LEFT JOIN provider_models pm ON pm.id = l.provider_model_id
                    WHERE l.created_at >= ?
                    GROUP BY p.ref, m.ref, pm.provider_model_name, l.agent_id
                """, (now - wsec,)).fetchall()
            except Exception:
                rows = []
        global_sum = {"requests": 0, "tokens_in": 0, "tokens_out": 0,
                      "tokens_thinking": 0, "cost": 0.0}
        by_provider: dict = {}
        by_model: dict = {}
        for r in rows:
            provider = r["provider_ref"] or "?"
            model = r["model_ref"] or "?"
            req, tin, tout, tthink = r["req"], r["tin"], r["tout"], r["tthink"]
            cost = r["cost"] or 0.0
            global_sum["requests"] += req or 0
            global_sum["tokens_in"] += tin or 0
            global_sum["tokens_out"] += tout or 0
            global_sum["tokens_thinking"] += tthink or 0
            global_sum["cost"] += cost
            p = by_provider.setdefault(provider, {"requests": 0, "tokens_in": 0,
                                                  "tokens_out": 0, "tokens_thinking": 0,
                                                  "cost": 0.0, "models": set()})
            p["requests"] += req or 0
            p["tokens_in"] += tin or 0
            p["tokens_out"] += tout or 0
            p["tokens_thinking"] += tthink or 0
            p["cost"] += cost
            p["models"].add(model)
            m = by_model.setdefault(model, {"provider": provider, "requests": 0,
                                            "tokens_in": 0, "tokens_out": 0,
                                            "tokens_thinking": 0, "cost": 0.0})
            m["requests"] += req or 0
            m["tokens_in"] += tin or 0
            m["tokens_out"] += tout or 0
            m["tokens_thinking"] += tthink or 0
            m["cost"] += cost
        global_sum["cost"] = round(global_sum["cost"], 6)
        # Dernier call réel (dans la fenêtre) : MAX(created_at) de model_call_log.
        global_sum["last_call"] = None
        if cat is not None:
            try:
                _r = cat.conn.execute(
                    "SELECT MAX(created_at) m FROM model_call_log "
                    "WHERE created_at >= ?", (now - wsec,)).fetchone()
                global_sum["last_call"] = _r["m"] if _r and _r["m"] else None
            except Exception:
                pass
        out[wname] = {
            "summary": global_sum,
            "providers": [dict(v, models=sorted(v["models"])) for v in by_provider.values()],
            "models": [dict(v) for v in by_model.values()],
        }
    return {"windows": out, "ts": now}


# ── Série temporelle (graphe) ────────────────────────────────────────────

def op_usage_series(params):
    """Série temporelle de consommation pour un graphe (token/min, req/min).

    params : { window: '5m'|'15m'|'1h'|'4h'|'24h'|'7j',
               dim: 'global'|'agent'|'provider'|'provider_model' (défaut global) }

    Retourne { points: [{bucket, label, requests, tokens_in, tokens_out}], ... }.
    Chaque point est un bucket de la granularité de la fenêtre. Les séries
    multi-voies (par agent/provider/modèle) sont regroupées par clé : pour
    `dim=provider`, la clé est provider_ref ; pour `agent`, agent_id ; pour
    `provider_model`, "provider/model".
    """
    import time as _t
    from services.api._shared import _get_rt
    rt = _get_rt()
    now = int(_t.time())
    wname = params.get("window", "1h")
    dim = params.get("dim", "global")
    wsec = _window_seconds(wname)
    gran = _series_granularity(wname)

    # Tables de cascade pertinentes (granularité <= fenêtre).
    CASCADE_TABLES = ("usage_history_1m", "usage_history_15m",
                      "usage_history_3h", "usage_history_1d",
                      "usage_history_1w", "usage_history_1mo")
    relevant = [t for t in CASCADE_TABLES if _bucket_size(t) <= wsec]
    if not relevant:
        return {"status": "error", "error": "fenêtre invalide"}

    # SELECT de groupe selon la dimension.
    if dim == "agent":
        group = "COALESCE(agent_id,'?')"
        key = "COALESCE(agent_id,'?')"
    elif dim == "provider":
        group = "COALESCE(provider_ref,'?')"
        key = "COALESCE(provider_ref,'?')"
    elif dim == "provider_model":
        group = "COALESCE(provider_ref,'?') || '/' || COALESCE(model_ref,'?')"
        key = "COALESCE(provider_ref,'?') || '/' || COALESCE(model_ref,'?')"
    else:
        group = "'global'"
        key = "'global'"

    try:
        unions = []
        for tbl in relevant:
            unions.append(f"""
                SELECT {group} AS grp,
                       CAST(bucket / {gran} AS INTEGER) * {gran} AS bkt,
                       SUM(requests) req, SUM(tokens_in) tin, SUM(tokens_out) tout
                FROM {tbl} WHERE bucket >= {now - wsec}
                GROUP BY 1, 2""")
        rows = rt.conn.execute(
            "SELECT grp, bkt, SUM(req) req, SUM(tin) tin, SUM(tout) tout "
            "FROM ( " + " UNION ALL ".join(unions) +
            " ) GROUP BY grp, bkt ORDER BY grp, bkt").fetchall()
    except Exception:
        rows = []

    # Regroupe par clé → liste de points.
    series: dict = {}
    for r in rows:
        g = r["grp"] or "global"
        s = series.setdefault(g, [])
        s.append({
            "bucket": r["bkt"],
            "requests": r["req"] or 0,
            "tokens_in": r["tin"] or 0,
            "tokens_out": r["tout"] or 0,
        })

    # Points global = fusion de toutes les voies.
    if dim == "global" and "global" in series:
        global_points = series["global"]
    else:
        merged: dict = {}
        for g, pts in series.items():
            for p in pts:
                b = merged.setdefault(p["bucket"], {"bucket": p["bucket"],
                                                    "requests": 0, "tokens_in": 0,
                                                    "tokens_out": 0})
                b["requests"] += p["requests"]
                b["tokens_in"] += p["tokens_in"]
                b["tokens_out"] += p["tokens_out"]
        global_points = [merged[k] for k in sorted(merged)]

    for pts in series.values():
        pts.sort(key=lambda p: p["bucket"])

    return {
        "status": "ok", "window": wname, "dim": dim,
        "granularity_s": gran,
        "series": series,
        "global": global_points,
        "ts": now,
    }


# ── Tarif ───────────────────────────────────────────────────────────────

def _op_tarif_info(_params):
    from services.tarif import tarif_info
    return tarif_info()


def _op_tarif_sync(url=None):
    from services.tarif import sync_tarif
    return sync_tarif(url)


# ── Batchage manuel ─────────────────────────────────────────────

def op_usage_batch_run(params=None):
    """Déclenche un cycle manuel du ticker de batchage (usage_batcher).

    Agrège model_call_log → usage_history_1m (buckets 1 min) puis cascade
    vers 15m/3h/1d/1w/1mo pour les buckets figés, et purge les TTL.

    params optionnels :
      - reconcile: true → fait aussi la réconciliation depuis l'archive.
      - start / end : timeframe de réconciliation (timestamps). Sans start/end,
        on réconcilie l'heure précédente + la veille (auto).
    """
    try:
        from modules.usage.usage_batcher import run_once, _reconcile_archive, _cat_conn, _rt_conn
        r = run_once()
        if params and params.get("reconcile"):
            cat = _cat_conn()
            rt = _rt_conn()
            import time as _t
            now = int(_t.time())
            start = int(params.get("start") or 0)
            end = int(params.get("end") or 0)
            if not (start and end):
                # défaut : heure précédente + veille (comme auto).
                cur_hour = (now // 3600) * 3600
                start = cur_hour - 3600
                end = cur_hour
            rec = _reconcile_archive(cat, rt, start, end)
            r["reconcile"] = rec
        return {"status": "ok", **r}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Route registration ─────────────────────────────────────────────────

register("usage/budget",    op_usage_budget)
register("usage/free_tier", op_usage_free_tier)
register("usage/monitor",   lambda p: _quiet(op_usage_monitor, p))
register("usage/series",    lambda p: _quiet(op_usage_series, p))
register("usage/batch/run", lambda p: _quiet(op_usage_batch_run, p))
register("tarif/info",      lambda p: _quiet(_op_tarif_info, p))
register("tarif/sync",      lambda p: _quiet(_op_tarif_sync, p.get("url")))


def op_usage_responded_models(params=None):
    """Modèles ayant déjà répondu, classés par score final.

    Tableau : score benchmark global × score latence × (1 − fail_rate)."""
    try:
        from modules.sql.runtime_repo import RuntimeDB
        rt = RuntimeDB()
        limit = int((params or {}).get("limit", 50))
        rows = rt.list_responded_models(limit=limit)
        rt.close()
        return {"status": "ok", "models": rows}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


register("usage/responded_models", lambda p: _quiet(op_usage_responded_models, p))
