"""StreamBus — Bus de diffusion des chunks d'exécution des agents (Phase 4).

Diffusion en mémoire (process daemon unique) des morceaux de texte produits
par les étapes `llm_call` des agents. Les clients (GUI) interrogent via
poll HTTP (`agent/stream`) et peuvent INTERJECTER en envoyant un signal
(`agent/signal`) que l'agent consomme en cours d'exécution.
"""

import threading
import time
from typing import Any, Dict, List, Optional


class StreamBus:
    """Buffer circulaire de chunks par agent_id."""

    def __init__(self, max_per_agent: int = 2000):
        self._lock = threading.Lock()
        self._buffers: Dict[int, List[Dict[str, Any]]] = {}
        self._seq = 0
        self._max = max_per_agent

    def publish(self, agent_id: int, chunk: str, kind: str = "token") -> int:
        with self._lock:
            self._seq += 1
            seq = self._seq
            buf = self._buffers.setdefault(agent_id, [])
            buf.append({
                "seq": seq,
                "ts": time.time(),
                "chunk": chunk,
                "kind": kind,
            })
            if len(buf) > self._max:
                del buf[0:len(buf) - self._max]
            return seq

    def since(self, agent_id: int, seq: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._buffers.get(agent_id, []) if e["seq"] > seq]

    def reset(self, agent_id: int) -> None:
        with self._lock:
            self._buffers.pop(agent_id, None)


# Singleton process-wide (un seul daemon)
stream_bus = StreamBus()
