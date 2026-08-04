import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def test_monitoring_metrics_route_registered():
    from services.api.router import ROUTES
    assert "monitoring/metrics" in ROUTES, "route monitoring/metrics manquante"


def test_monitoring_metrics_payload_shape():
    from services.api.router import dispatch
    result = dispatch("monitoring/metrics", {})
    assert isinstance(result, dict)
    assert "provider_metrics" in result
    assert "usage_summary" in result
    assert "system_status" in result
    assert isinstance(result["provider_metrics"], dict)
    assert isinstance(result["usage_summary"], dict)
    assert isinstance(result["system_status"], dict)


def test_monitoring_metrics_system_status_keys():
    from services.api.router import dispatch
    result = dispatch("monitoring/metrics", {})
    status = result["system_status"]
    assert "daemon" in status
    assert "version" in status
    assert "api_version" in status
