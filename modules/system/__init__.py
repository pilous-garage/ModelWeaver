"""Module système : dépendances et collecte de métriques."""
from modules.system.deps import install_system_package, install_target_dependencies
from modules.system.metrics_collector import (
    SystemMetricsCollector,
    CPUMetrics,
    MemoryMetrics,
    ProcessMetrics,
    SystemMetricsSnapshot,
    get_system_metrics_collector,
    collect_cpu,
    collect_memory,
    collect_processes,
    collect_snapshot,
    snapshot_to_dict,
    snapshot_to_text,
)

__all__ = [
    "install_system_package",
    "install_target_dependencies",
    "SystemMetricsCollector",
    "CPUMetrics",
    "MemoryMetrics",
    "ProcessMetrics",
    "SystemMetricsSnapshot",
    "get_system_metrics_collector",
    "collect_cpu",
    "collect_memory",
    "collect_processes",
    "collect_snapshot",
    "snapshot_to_dict",
    "snapshot_to_text",
]
