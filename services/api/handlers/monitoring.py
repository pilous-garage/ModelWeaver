import time

from services.api._shared import _quiet
from services.api.router import register


def op_monitoring_metrics(params):
    """Métriques de monitoring exposées par le framework.

    Retourne :
      - provider_metrics : métriques en mémoire du TelemetryCollector
        (latence, taux d'erreur, disponibilité)
      - usage_summary : consommation agrégée par fenêtres 1h/24h/7j
        (requêtes, tokens, coût) via les historiques usage
      - system_status : état système minimal (daemon, usage collector,
        modèle sync, superviseur) et version
    """
    return {
        "provider_metrics": _provider_metrics(),
        "usage_summary": _usage_summary(),
        "system_status": _system_status(),
        "recent_llm": _recent_llm(),
    }


def _recent_llm(hours: int = 24, limit: int = 50):
    """Derniers LLM réellement appelés (model_call_log, source de vérité).

    Agrége par (provider, modèle) : requêtes, tokens in/out/thinking,
    échecs, taux d'erreur, codes d'erreur (rate_limit…) et latence moyenne.
    Le modèle est résolu par nom quand l'ID n'existe pas (modèles non
    présents au catalogue) via provider_model_name du log (prov_model).
    """
    try:
        from services.api._shared import _get_cat
        cat = _get_cat()
        now = int(time.time())
        rows = cat.conn.execute("""
            SELECT caller_id, provider_ref, model_ref, requests, ok_req, err_req,
                   tokens_in, tokens_out, tokens_thinking, latency_ms,
                   error_codes, last_call FROM (
              SELECT COALESCE(NULLIF(l.caller_id, ''), '?') AS caller_id,
                     p.ref AS provider_ref,
                     COALESCE(m.ref, pm.provider_model_name, '?') AS model_ref,
                     COUNT(*) AS requests,
                     SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END) AS ok_req,
                     SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END) AS err_req,
                     SUM(tokens_in)  AS tokens_in,
                     SUM(tokens_out) AS tokens_out,
                     SUM(tokens_thinking) AS tokens_thinking,
                     ROUND(AVG(latency_ms), 1) AS latency_ms,
                     GROUP_CONCAT(DISTINCT error_code) AS error_codes,
                     MAX(l.created_at) AS last_call
              FROM model_call_log l
              LEFT JOIN catalogue_providers p ON p.id = l.provider_id
              LEFT JOIN catalogue_models m   ON m.id = l.model_id
              LEFT JOIN provider_models pm   ON pm.id = l.provider_model_id
              WHERE l.created_at >= ?
              GROUP BY l.caller_id, l.provider_id, l.model_id, l.provider_model_id
              UNION ALL
              SELECT COALESCE(NULLIF(l.caller_id, ''), '?'), p.ref,
                     COALESCE(m.ref, pm.provider_model_name, '?'),
                     COUNT(*), SUM(CASE WHEN success = 1 THEN 1 ELSE 0 END),
                     SUM(CASE WHEN success = 0 THEN 1 ELSE 0 END),
                     SUM(tokens_in), SUM(tokens_out), SUM(tokens_thinking),
                     ROUND(AVG(latency_ms), 1),
                     GROUP_CONCAT(DISTINCT error_code),
                     MAX(l.created_at)
              FROM model_call_log_archive l
              LEFT JOIN catalogue_providers p ON p.id = l.provider_id
              LEFT JOIN catalogue_models m   ON m.id = l.model_id
              LEFT JOIN provider_models pm   ON pm.id = l.provider_model_id
              WHERE l.created_at >= ?
              GROUP BY l.caller_id, l.provider_id, l.model_id, l.provider_model_id
            )
            GROUP BY caller_id, provider_ref, model_ref
            ORDER BY last_call DESC
            LIMIT ?
        """, (now - hours * 3600, now - hours * 3600, limit)).fetchall()
        out = []
        for r in rows:
            req = r["requests"] or 0
            err = r["err_req"] or 0
            out.append({
                "caller_id": r["caller_id"] or "?",
                "provider": r["provider_ref"] or "?",
                "model": r["model_ref"] or "?",
                "requests": req,
                "errors": err,
                "error_rate": round(100.0 * err / req, 1) if req else 0.0,
                "tokens_in": r["tokens_in"] or 0,
                "tokens_out": r["tokens_out"] or 0,
                "tokens_thinking": r["tokens_thinking"] or 0,
                "latency_ms": r["latency_ms"],
                "error_codes": [c for c in (r["error_codes"] or "").split(",") if c],
                "last_call": r["last_call"],
            })
        return {"hours": hours, "calls": out}
    except Exception as e:
        return {"error": str(e), "calls": []}


def _provider_metrics():
    try:
        from modules.telemetry.telemetry_collector import get_collector
        collector = get_collector()
        metrics = collector.get_all_metrics()
        return {
            name: {
                "provider_name": m.provider_name,
                "latency_ms": m.latency_ms,
                "error_rate": m.error_rate,
                "is_up": m.is_up,
                "timestamp": _iso(m.timestamp),
            }
            for name, m in metrics.items()
        }
    except Exception:
        return {}


def _usage_summary():
    try:
        from services.api.handlers.usage import op_usage_monitor
        return _quiet(op_usage_monitor, {})
    except Exception:
        return {"windows": {}, "ts": None}


def _system_status():
    try:
        from services._common import mw_home
        mw_dir = mw_home()
        status = {
            "daemon": {
                "token_present": (mw_dir / "api.token").exists(),
                "port_file_present": (mw_dir / "api.port").exists(),
            },
            "usage_collector": {
                "pidfile_present": (mw_dir / "run" / "usage_collector.pid").exists(),
            },
            "model_sync": {
                "pidfile_present": (mw_dir / "run" / "model_sync.pid").exists(),
            },
            "version": None,
            "api_version": None,
        }
        try:
            from services.api._shared import MW_VERSION, API_VERSION
            status["version"] = MW_VERSION
            status["api_version"] = API_VERSION
        except Exception:
            pass
        return status
    except Exception:
        return {"error": "unavailable"}


def _iso(value):
    try:
        return value.isoformat() + "Z" if value else None
    except Exception:
        return None


register("monitoring/metrics", lambda p: _quiet(op_monitoring_metrics, p))
