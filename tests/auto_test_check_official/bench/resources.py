"""resources — Moniteur de ressources pour les benchmarks FSM.

Échantillonne {cpu%, ram_mb, fds} dans un thread, et mesure la taille de la
BDD. Retourne un rapport {min, max, moy} à la fin.

Usage :
    mon = ResourceMonitor(db_path=...)
    mon.start()
    ...  # le benchmark tourne
    report = mon.stop()   # {cpu: {...}, ram_mb: {...}, fds: {...}, db_size_mb}
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional


class ResourceMonitor:
    def __init__(self, db_path: Optional[Path] = None,
                 interval_s: float = 2.0):
        self._interval = interval_s
        self._db_path = db_path
        self._stop = threading.Event()
        self._samples: Dict[str, list] = {"cpu": [], "ram_mb": [], "fds": []}
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        while not self._stop.is_set():
            self._sample()
            self._stop.wait(self._interval)
        self._sample()   # dernière mesure

    def _sample(self) -> None:
        cpu = _cpu_percent()
        ram = _ram_mb()
        fds = _fd_count()
        with self._lock:
            self._samples["cpu"].append(cpu)
            self._samples["ram_mb"].append(ram)
            self._samples["fds"].append(fds)

    def stop(self) -> Dict[str, dict]:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)
        with self._lock:
            out = {k: _stats(v) for k, v in self._samples.items()}
        if self._db_path:
            try:
                out["db_size_mb"] = round(
                    Path(self._db_path).stat().st_size / (1024 * 1024), 2)
            except OSError:
                out["db_size_mb"] = 0.0
        return out


def _stats(values: list) -> dict:
    if not values:
        return {"min": 0, "max": 0, "avg": 0, "n": 0}
    return {"min": round(min(values), 2), "max": round(max(values), 2),
            "avg": round(sum(values) / len(values), 2), "n": len(values)}


def _cpu_percent() -> float:
    try:
        with open("/proc/self/stat") as f:
            parts = f.read().split()
        # utime+stime (ticks) / HZ → approximation du temps CPU cumulé
        utime, stime = int(parts[13]), int(parts[14])
        try:
            hz = os.sysconf("SC_CLK_TCK")
        except (ValueError, AttributeError):
            hz = 100
        return round((utime + stime) / hz, 2)   # secondes CPU cumulées
    except Exception:
        return 0.0


def _ram_mb() -> float:
    try:
        with open("/proc/self/status") as f:
            for line in f:
                if line.startswith("VmRSS:"):
                    return round(int(line.split()[1]) / 1024, 2)   # kB → MB
    except Exception:
        pass
    return 0.0


def _fd_count() -> int:
    try:
        return len(os.listdir("/proc/self/fd"))
    except Exception:
        return 0
