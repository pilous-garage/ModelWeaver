from services.api._shared import _quiet
from services.api.router import register

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

    Sources : usage_history_1m (1h), usage_history_1h (24h/7j).
    Retourne pour chaque fenêtre un résumé global + la répartition par
    provider et par modèle (requests, tokens in/out/thinking, cost).
    """
    import time as _t
    from services.api._shared import _get_rt
    rt = _get_rt()
    now = int(_t.time())
    windows = {"1h": 3600, "24h": 86400, "7j": 7 * 86400}
    out = {}
    for wname, wsec in windows.items():
        cutoff_1h = now - 3600
        if wname == "1h":
            # fenêtre courte : détail 1m (gardé 24h) suffit, plus précis
            rows = rt.conn.execute("""
                SELECT provider_ref, model_ref, agent_id,
                       SUM(requests) req, SUM(tokens_in) tin,
                       SUM(tokens_out) tout, SUM(tokens_thinking) tthink,
                       SUM(cost) cost
                FROM usage_history_1m
                WHERE bucket >= ?
                GROUP BY provider_ref, model_ref, agent_id
                ORDER BY cost DESC
            """, (now - wsec,)).fetchall()
        else:
            # fenêtres larges : historique 1h (30j)
            rows = rt.conn.execute("""
                SELECT provider_ref, model_ref, agent_id,
                       SUM(requests) req, SUM(tokens_in) tin,
                       SUM(tokens_out) tout, SUM(tokens_thinking) tthink,
                       SUM(cost) cost
                FROM usage_history_1h
                WHERE bucket >= ?
                GROUP BY provider_ref, model_ref, agent_id
                ORDER BY cost DESC
            """, (now - wsec,)).fetchall()
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


# ── Route registration ─────────────────────────────────────────────────

register("usage/budget",    op_usage_budget)
register("usage/free_tier", op_usage_free_tier)
register("usage/monitor",   lambda p: _quiet(op_usage_monitor, p))
register("tarif/info",      lambda p: _quiet(_op_tarif_info, p))
register("tarif/sync",      lambda p: _quiet(_op_tarif_sync, p.get("url")))
