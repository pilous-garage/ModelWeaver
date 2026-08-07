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
    windows = {"1h": 3600, "24h": 86400, "7j": 7 * 86400}
    out = {}
    cat = _get_cat()
    for wname, wsec in windows.items():
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
                rows = rt.conn.execute(sql).fetchall()
            except Exception:
                rows = None
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
        out[wname] = {
            "summary": global_sum,
            "providers": [dict(v, models=sorted(v["models"])) for v in by_provider.values()],
            "models": [dict(v) for v in by_model.values()],
        }
    return {"windows": out, "ts": now}


# ── Tarif ───────────────────────────────────────────────────────────────

def _op_tarif_info(_params):
    from services.tarif import tarif_info
    return tarif_info()


def _op_tarif_sync(url=None):
    from services.tarif import sync_tarif
    return sync_tarif(url)


# ── Batchage manuel ─────────────────────────────────────────────

def op_usage_batch_run(_params=None):
    """Déclenche un cycle manuel du ticker de batchage (usage_batcher).

    Agrège model_call_log → usage_history_1m (buckets 1 min) puis cascade
    vers 15m/3h/1d/1w/1mo pour les buckets figés, et purge les TTL.
    """
    try:
        from modules.usage.usage_batcher import run_once
        r = run_once()
        return {"status": "ok", **r}
    except Exception as e:
        return {"status": "error", "error": str(e)}


# ── Route registration ─────────────────────────────────────────────────

register("usage/budget",    op_usage_budget)
register("usage/free_tier", op_usage_free_tier)
register("usage/monitor",   lambda p: _quiet(op_usage_monitor, p))
register("usage/batch/run", lambda p: _quiet(op_usage_batch_run, p))
register("tarif/info",      lambda p: _quiet(_op_tarif_info, p))
register("tarif/sync",      lambda p: _quiet(_op_tarif_sync, p.get("url")))
