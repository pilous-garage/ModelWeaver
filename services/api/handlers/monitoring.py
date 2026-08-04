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
    }


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
