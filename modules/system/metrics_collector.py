"""Système de collecte de métriques système (CPU, RAM, tâches en cours).

Fournit un collecteur de métriques système léger basé sur psutil,
intégrable dans le pipeline de télémétrie du framework ModelWeaver.
"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional

try:
    import psutil
    _PSUTIL_AVAILABLE = True
except ImportError:
    _PSUTIL_AVAILABLE = False


@dataclass
class CPUMetrics:
    """Métriques CPU."""
    cpu_percent: float = 0.0
    cpu_count_physical: int = 0
    cpu_count_logical: int = 0
    cpu_freq_current_mhz: float = 0.0
    cpu_freq_min_mhz: float = 0.0
    cpu_freq_max_mhz: float = 0.0
    load_avg_1m: float = 0.0
    load_avg_5m: float = 0.0
    load_avg_15m: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class MemoryMetrics:
    """Métriques mémoire (RAM)."""
    total_gb: float = 0.0
    available_gb: float = 0.0
    used_gb: float = 0.0
    percent_used: float = 0.0
    swap_total_gb: float = 0.0
    swap_used_gb: float = 0.0
    swap_percent_used: float = 0.0
    timestamp: datetime = field(default_factory=datetime.utcnow)


@dataclass
class ProcessMetrics:
    """Métriques d'un processus individuel (tâche en cours)."""
    pid: int = 0
    name: str = ""
    status: str = ""
    cpu_percent: float = 0.0
    memory_rss_mb: float = 0.0
    memory_vms_mb: float = 0.0
    num_threads: int = 0
    open_files: int = 0
    connections: int = 0
    create_time: Optional[str] = None
    cmdline: List[str] = field(default_factory=list)


@dataclass
class SystemMetricsSnapshot:
    """Instantané complet des métriques système."""
    cpu: CPUMetrics = field(default_factory=CPUMetrics)
    memory: MemoryMetrics = field(default_factory=MemoryMetrics)
    processes: List[ProcessMetrics] = field(default_factory=list)
    process_count: int = 0
    timestamp: datetime = field(default_factory=datetime.utcnow)


class SystemMetricsCollector:
    """Collecteur de métriques système (CPU, RAM, tâches en cours).

    Utilise psutil pour récupérer les métriques. Si psutil n'est pas
    disponible, toutes les valeurs sont à zéro / vides.
    """

    def __init__(self):
        self._available = _PSUTIL_AVAILABLE

    @property
    def is_available(self) -> bool:
        """Retourne True si psutil est installé et fonctionnel."""
        return self._available

    # ------------------------------------------------------------------ CPU
    def collect_cpu_metrics(self) -> CPUMetrics:
        """Collecte les métriques CPU."""
        if not self._available:
            return CPUMetrics()

        try:
            cpu_percent = psutil.cpu_percent(interval=0.1)
            cpu_count_physical = psutil.cpu_count(logical=False) or 0
            cpu_count_logical = psutil.cpu_count(logical=True) or 0

            freq = psutil.cpu_freq()
            cpu_freq_current_mhz = freq.current if freq else 0.0
            cpu_freq_min_mhz = freq.min if freq else 0.0
            cpu_freq_max_mhz = freq.max if freq else 0.0

            load_avg = psutil.getloadavg() if hasattr(psutil, "getloadavg") else (0.0, 0.0, 0.0)

            return CPUMetrics(
                cpu_percent=cpu_percent,
                cpu_count_physical=cpu_count_physical,
                cpu_count_logical=cpu_count_logical,
                cpu_freq_current_mhz=cpu_freq_current_mhz,
                cpu_freq_min_mhz=cpu_freq_min_mhz,
                cpu_freq_max_mhz=cpu_freq_max_mhz,
                load_avg_1m=load_avg[0],
                load_avg_5m=load_avg[1],
                load_avg_15m=load_avg[2],
            )
        except Exception:
            return CPUMetrics()

    # ------------------------------------------------------------------ RAM
    def collect_memory_metrics(self) -> MemoryMetrics:
        """Collecte les métriques mémoire (RAM + swap)."""
        if not self._available:
            return MemoryMetrics()

        try:
            mem = psutil.virtual_memory()
            swap = psutil.swap_memory()

            return MemoryMetrics(
                total_gb=round(mem.total / (1024 ** 3), 2),
                available_gb=round(mem.available / (1024 ** 3), 2),
                used_gb=round(mem.used / (1024 ** 3), 2),
                percent_used=mem.percent,
                swap_total_gb=round(swap.total / (1024 ** 3), 2),
                swap_used_gb=round(swap.used / (1024 ** 3), 2),
                swap_percent_used=swap.percent,
            )
        except Exception:
            return MemoryMetrics()

    # ------------------------------------------------------------------ PROCESSES
    def collect_process_metrics(self, top_n: int = 10) -> List[ProcessMetrics]:
        """Collecte les métriques des processus en cours (top N par CPU)."""
        if not self._available:
            return []

        processes: List[ProcessMetrics] = []
        try:
            for proc in psutil.process_iter(
                attrs=[
                    "pid", "name", "status", "cpu_percent",
                    "memory_info", "num_threads", "open_files",
                    "num_connections", "create_time", "cmdline",
                ],
            ):
                try:
                    info = proc.info
                    mem_info = info.get("memory_info")
                    rss_mb = round(mem_info.rss / (1024 ** 2), 2) if mem_info else 0.0
                    vms_mb = round(mem_info.vms / (1024 ** 2), 2) if mem_info else 0.0

                    create_time = None
                    ct = info.get("create_time")
                    if ct is not None:
                        create_time = datetime.fromtimestamp(ct).isoformat()

                    processes.append(ProcessMetrics(
                        pid=info.get("pid", 0),
                        name=info.get("name", ""),
                        status=info.get("status", ""),
                        cpu_percent=info.get("cpu_percent", 0.0) or 0.0,
                        memory_rss_mb=rss_mb,
                        memory_vms_mb=vms_mb,
                        num_threads=info.get("num_threads", 0),
                        open_files=len(info.get("open_files") or []),
                        connections=len(info.get("num_connections") or []),
                        create_time=create_time,
                        cmdline=info.get("cmdline") or [],
                    ))
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
        except Exception:
            return []

        # Trier par CPU décroissant et garder le top N
        processes.sort(key=lambda p: p.cpu_percent, reverse=True)
        return processes[:top_n]

    # ------------------------------------------------------------------ SNAPSHOT
    def collect_snapshot(self, top_n: int = 10) -> SystemMetricsSnapshot:
        """Collecte un instantané complet des métriques système."""
        return SystemMetricsSnapshot(
            cpu=self.collect_cpu_metrics(),
            memory=self.collect_memory_metrics(),
            processes=self.collect_process_metrics(top_n=top_n),
            process_count=len(self.collect_process_metrics(top_n=9999)),
        )

    # ------------------------------------------------------------------ FORMATTING
    def snapshot_to_dict(self, snapshot: Optional[SystemMetricsSnapshot] = None,
                         top_n: int = 10) -> dict:
        """Retourne un instantané sous forme de dictionnaire sérialisable."""
        if snapshot is None:
            snapshot = self.collect_snapshot(top_n=top_n)

        return {
            "timestamp": snapshot.timestamp.isoformat(),
            "cpu": {
                "cpu_percent": snapshot.cpu.cpu_percent,
                "cpu_count_physical": snapshot.cpu.cpu_count_physical,
                "cpu_count_logical": snapshot.cpu.cpu_count_logical,
                "cpu_freq_current_mhz": snapshot.cpu.cpu_freq_current_mhz,
                "cpu_freq_min_mhz": snapshot.cpu.cpu_freq_min_mhz,
                "cpu_freq_max_mhz": snapshot.cpu.cpu_freq_max_mhz,
                "load_avg_1m": snapshot.cpu.load_avg_1m,
                "load_avg_5m": snapshot.cpu.load_avg_5m,
                "load_avg_15m": snapshot.cpu.load_avg_15m,
            },
            "memory": {
                "total_gb": snapshot.memory.total_gb,
                "available_gb": snapshot.memory.available_gb,
                "used_gb": snapshot.memory.used_gb,
                "percent_used": snapshot.memory.percent_used,
                "swap_total_gb": snapshot.memory.swap_total_gb,
                "swap_used_gb": snapshot.memory.swap_used_gb,
                "swap_percent_used": snapshot.memory.swap_percent_used,
            },
            "processes": [
                {
                    "pid": p.pid,
                    "name": p.name,
                    "status": p.status,
                    "cpu_percent": p.cpu_percent,
                    "memory_rss_mb": p.memory_rss_mb,
                    "memory_vms_mb": p.memory_vms_mb,
                    "num_threads": p.num_threads,
                    "open_files": p.open_files,
                    "connections": p.connections,
                    "create_time": p.create_time,
                    "cmdline": p.cmdline,
                }
                for p in snapshot.processes
            ],
            "process_count": snapshot.process_count,
        }

    def snapshot_to_text(self, snapshot: Optional[SystemMetricsSnapshot] = None,
                         top_n: int = 10) -> str:
        """Retourne un instantané sous forme de texte lisible."""
        if snapshot is None:
            snapshot = self.collect_snapshot(top_n=top_n)

        lines = [
            "=== System Metrics Snapshot ===",
            f"Timestamp : {snapshot.timestamp.isoformat()}",
            "",
            "--- CPU ---",
            f"  Usage         : {snapshot.cpu.cpu_percent}%",
            f"  Cores physical: {snapshot.cpu.cpu_count_physical}",
            f"  Cores logical : {snapshot.cpu.cpu_count_logical}",
            f"  Freq (MHz)    : cur={snapshot.cpu.cpu_freq_current_mhz:.1f} "
            f"min={snapshot.cpu.cpu_freq_min_mhz:.1f} "
            f"max={snapshot.cpu.cpu_freq_max_mhz:.1f}",
            f"  Load avg      : 1m={snapshot.cpu.load_avg_1m:.2f} "
            f"5m={snapshot.cpu.load_avg_5m:.2f} "
            f"15m={snapshot.cpu.load_avg_15m:.2f}",
            "",
            "--- Memory (RAM) ---",
            f"  Total   : {snapshot.memory.total_gb:.2f} GB",
            f"  Used    : {snapshot.memory.used_gb:.2f} GB "
            f"({snapshot.memory.percent_used}%)",
            f"  Avail.  : {snapshot.memory.available_gb:.2f} GB",
            "",
            "--- Swap ---",
            f"  Total   : {snapshot.memory.swap_total_gb:.2f} GB",
            f"  Used    : {snapshot.memory.swap_used_gb:.2f} GB "
            f"({snapshot.memory.swap_percent_used}%)",
            "",
            f"--- Processes (top {top_n} by CPU, total={snapshot.process_count}) ---",
        ]

        for p in snapshot.processes:
            cmd_str = " ".join(p.cmdline[:3]) if p.cmdline else ""
            lines.append(
                f"  PID={p.pid:<6} CPU={p.cpu_percent:>5.1f}% "
                f"RSS={p.memory_rss_mb:>7.1f}MB "
                f"THR={p.num_threads:<3} "
                f"{p.name} {cmd_str}"
            )

        return "\n".join(lines)


# Singleton de collecteur au niveau du module système
_system_collector = SystemMetricsCollector()


def get_system_metrics_collector() -> SystemMetricsCollector:
    """Retourne l'instance singleton du collecteur de métriques système."""
    return _system_collector


def collect_cpu() -> CPUMetrics:
    """Raccourci : collecte rapide des métriques CPU."""
    return _system_collector.collect_cpu_metrics()


def collect_memory() -> MemoryMetrics:
    """Raccourci : collecte rapide des métriques mémoire."""
    return _system_collector.collect_memory_metrics()


def collect_processes(top_n: int = 10) -> List[ProcessMetrics]:
    """Raccourci : collecte rapide des processus (top N)."""
    return _system_collector.collect_process_metrics(top_n=top_n)


def collect_snapshot(top_n: int = 10) -> SystemMetricsSnapshot:
    """Raccourci : collecte un instantané complet."""
    return _system_collector.collect_snapshot(top_n=top_n)


def snapshot_to_dict(top_n: int = 10) -> dict:
    """Raccourci : instantané sous forme de dict."""
    return _system_collector.snapshot_to_dict(top_n=top_n)


def snapshot_to_text(top_n: int = 10) -> str:
    """Raccourci : instantané sous forme de texte."""
    return _system_collector.snapshot_to_text(top_n=top_n)
