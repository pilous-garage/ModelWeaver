import time
import threading
from typing import Dict, Tuple

_EMPTY = object()

class RateLimitExceeded(Exception):
    def __init__(self, limit: int, window: int, retry_after: int, kind: str = "req"):
        self.limit = limit
        self.window = window
        self.retry_after = retry_after
        self.kind = kind
        unit = kind if kind == "req" else "token"
        super().__init__(f"rate limit exceeded: {limit} {unit}/{window}s, retry in {retry_after}s")


class RateLimiter:
    def __init__(self):
        self._buckets: Dict[str, list] = {}
        self._lock = threading.Lock()
        self._cleanup_interval = 300
        self._last_cleanup = 0.0

    def check(self, key: str, limit: int, window: float = 60.0, weight: int = 1):
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup(now)
            events = self._buckets.get(key)
            if events is None:
                self._buckets[key] = [(now, weight)]
                return
            cutoff = now - window
            if events[0][0] < cutoff:
                events[:] = [(t, w) for t, w in events if t > cutoff]
            events.append((now, weight))
            total = sum(w for _, w in events)
            if total > limit:
                retry_after = int(events[0][0] + window - now) + 1
                raise RateLimitExceeded(limit, int(window), retry_after, "token" if weight > 1 else "req")

    def record(self, key: str, window: float = 60.0, weight: int = 1):
        now = time.monotonic()
        with self._lock:
            if now - self._last_cleanup > self._cleanup_interval:
                self._cleanup(now)
            events = self._buckets.get(key)
            if events is None:
                self._buckets[key] = [(now, weight)]
                return
            cutoff = now - window
            if events[0][0] < cutoff:
                events[:] = [(t, w) for t, w in events if t > cutoff]
            events.append((now, weight))

    def _cleanup(self, now: float):
        cutoff = now - self._cleanup_interval * 2
        stale = [k for k, v in self._buckets.items() if v and v[-1][0] < cutoff]
        for k in stale:
            del self._buckets[k]
        self._last_cleanup = now


_INSTANCE: RateLimiter = _EMPTY


def _get():
    global _INSTANCE
    if _INSTANCE is _EMPTY:
        _INSTANCE = RateLimiter()
    return _INSTANCE


def check_rate_limit(route: str, client_ip: str, tokens: int = 0) -> None:
    rl = _get()
    route_lower = route.lower()
    if route_lower in ("health",):
        return
    base_key = f"{client_ip}:{route_lower}"

    # ── req/min (existant) ──
    if route_lower in ("capabilities",):
        rl.check(f"r:{base_key}", limit=100, window=60)
    elif route_lower.startswith("keys/"):
        rl.check(f"r:{base_key}", limit=10, window=60)
    elif route_lower.startswith("tools/"):
        rl.check(f"r:{base_key}", limit=10, window=60)
    elif route_lower.startswith("agents/") and any(
        route_lower.endswith(sfx) for sfx in ("/spawn", "/delete")
    ):
        rl.check(f"r:{base_key}", limit=20, window=60)
    elif route_lower.startswith("agents/"):
        rl.check(f"r:{base_key}", limit=60, window=60)
    elif route_lower in ("llm/chat", "chat/session/send",
                         "llm/chat/stream", "chat/session/stream",
                         "agent/chat", "agent/stream"):
        rl.check(f"r:{base_key}", limit=30, window=60)
    else:
        rl.check(f"r:{base_key}", limit=30, window=60)

    # ── req/day ──
    rl.check(f"d:{base_key}", limit=5000, window=86400)

    # ── token/min ──
    if tokens:
        rl.check(f"t:m:{base_key}", limit=50000, window=60, weight=tokens)

    # ── token/day ──
    if tokens:
        rl.check(f"t:d:{base_key}", limit=1_000_000, window=86400, weight=tokens)
