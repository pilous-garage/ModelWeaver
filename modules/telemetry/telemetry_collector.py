from modules.telemetry.provider_metrics import ProviderMetrics, TelemetryCollector

_collector = TelemetryCollector()


def get_collector() -> TelemetryCollector:
    return _collector
