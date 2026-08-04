import time
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional

@dataclass
class ProviderMetrics:
    provider_name: str
    latency_ms: float
    error_rate: float
    is_up: bool
    timestamp: datetime = datetime.utcnow()

class TelemetryCollector:
    def __init__(self):
        self.metrics: Dict[str, ProviderMetrics] = {}

    def record_metrics(self, provider_name: str, latency_ms: float, error_rate: float, is_up: bool):
        self.metrics[provider_name] = ProviderMetrics(
            provider_name=provider_name,
            latency_ms=latency_ms,
            error_rate=error_rate,
            is_up=is_up
        )

    def get_metrics(self, provider_name: str) -> Optional[ProviderMetrics]:
        return self.metrics.get(provider_name)

    def get_all_metrics(self) -> Dict[str, ProviderMetrics]:
        return self.metrics
