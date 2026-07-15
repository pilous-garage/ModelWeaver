import json
import os
import time
import threading
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from urllib.request import urlopen, Request
from urllib.error import URLError

from services._common import mw_home
from services.ratelimit import _get as _get_rl, RateLimitExceeded

_EMPTY = object()

DEFAULT_TARIF_URL = os.environ.get(
    "MODELWEAVER_TARIF_URL",
    "http://localhost:8765/api/tarif",
)


class TarifError(RuntimeError):
    pass


class TarifManager:
    def __init__(self):
        self._data: Dict[str, Any] = {}
        self._lock = threading.Lock()
        self._mtime: float = 0.0

    # ── Seed ─────────────────────────────────────────────────

    def seed_default(self) -> None:
        src = Path(__file__).resolve().parent.parent / "modules" / "data" / "tarif.json"
        if not src.exists():
            raise TarifError(f"tarif.json par défaut introuvable: {src}")
        dst = self.path()
        dst.parent.mkdir(parents=True, exist_ok=True)
        raw = src.read_text(encoding="utf-8")
        dst.write_text(raw, encoding="utf-8")
        with self._lock:
            self._data = json.loads(raw)
            self._mtime = dst.stat().st_mtime

    # ── Chargement ────────────────────────────────────────────

    def path(self) -> Path:
        return mw_home() / "tarif.json"

    def load(self, path: Optional[Path] = None) -> bool:
        p = path or self.path()
        if not p.exists():
            return False
        try:
            raw = p.read_text(encoding="utf-8")
            data = json.loads(raw)
            with self._lock:
                self._data = data
                self._mtime = p.stat().st_mtime
            return True
        except (json.JSONDecodeError, OSError) as e:
            raise TarifError(f"tarif.json invalide: {e}") from e

    def fetch(self, url: Optional[str] = None) -> int:
        url = url or DEFAULT_TARIF_URL
        try:
            req = Request(url, headers={"User-Agent": "ModelWeaver/1.0"})
            with urlopen(req, timeout=30) as resp:
                raw = resp.read().decode()
            data = json.loads(raw)
        except (URLError, json.JSONDecodeError, OSError) as e:
            raise TarifError(f"fetch tarif échoué: {e}") from e
        p = self.path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(raw, encoding="utf-8")
        with self._lock:
            self._data = data
            self._mtime = p.stat().st_mtime
        return len(raw)

    # ── Consultation ──────────────────────────────────────────

    def _tiers(self) -> Dict[str, Any]:
        return self._data.get("tiers", {})

    def _providers(self) -> Dict[str, Any]:
        return self._data.get("providers", {})

    def effective_tier(self, provider_ref: str, model_ref: str) -> Optional[str]:
        prov = self._providers().get(provider_ref)
        if prov is None:
            return None
        if prov.get("unlimited"):
            return None
        model_conf = prov.get("models", {}).get(model_ref, {})
        tier = model_conf.get("tier") or prov.get("tier")
        return tier

    def effective_limits(self, provider_ref: str, model_ref: str) -> Dict[str, Any]:
        prov = self._providers().get(provider_ref)
        if prov is None:
            return {}
        if prov.get("unlimited"):
            return {"unlimited": True}
        tiers = self._tiers()
        model_conf = prov.get("models", {}).get(model_ref, {})
        tier_name = model_conf.get("tier") or prov.get("tier")
        if not tier_name:
            return {}
        base = dict(tiers.get(tier_name, {}))
        overrides = prov.get("overrides", {})
        base.update(overrides)
        model_overrides = {k: v for k, v in model_conf.items() if k != "tier"}
        base.update(model_overrides)
        return base

    def is_unlimited(self, provider_ref: str) -> bool:
        prov = self._providers().get(provider_ref)
        return bool(prov.get("unlimited")) if prov else False

    def raw(self) -> Dict[str, Any]:
        return dict(self._data)


_INSTANCE: TarifManager = _EMPTY


def _get():
    global _INSTANCE
    if _INSTANCE is _EMPTY:
        _INSTANCE = TarifManager()
        try:
            if not _INSTANCE.load():
                _INSTANCE.seed_default()
        except TarifError:
            pass
    return _INSTANCE


def get_limits(provider_ref: str, model_ref: str) -> Dict[str, Any]:
    return _get().effective_limits(provider_ref, model_ref)


def get_tier(provider_ref: str, model_ref: str) -> Optional[str]:
    return _get().effective_tier(provider_ref, model_ref)


def sync_tarif(url: Optional[str] = None) -> dict:
    m = _get()
    try:
        size = m.fetch(url)
        return {"status": "ok", "bytes": size, "providers": len(m._providers())}
    except TarifError as e:
        return {"status": "error", "error": str(e)}


def tarif_info() -> dict:
    m = _get()
    raw = m.raw()
    return {
        "status": "ok",
        "version": raw.get("meta", {}).get("version"),
        "updated_at": raw.get("meta", {}).get("updated_at"),
        "tiers": list(raw.get("tiers", {}).keys()),
        "providers": list(raw.get("providers", {}).keys()),
        "loaded": bool(raw),
    }


# ── Budget tracker ──────────────────────────────────────────

def check_budget(provider_ref: str, model_ref: str) -> dict:
    """Vérifie le budget disponible pour un (provider, model).
    Retourne un dict {ok, remaining_tokens_min, remaining_tokens_day,
    remaining_req_min, remaining_req_day, unlimited, error?}."""
    limits = _get().effective_limits(provider_ref, model_ref)
    if limits.get("unlimited"):
        return {"ok": True, "unlimited": True}

    rl = _get_rl()
    now = time.monotonic()
    info = {"ok": True, "unlimited": False}

    for key, limit_key, window in [
        (f"t:m:{provider_ref}:{model_ref}", "tokens_per_min", 60),
        (f"t:d:{provider_ref}:{model_ref}", "tokens_per_day", 86400),
        (f"r:m:{provider_ref}:{model_ref}", "req_per_min", 60),
        (f"r:d:{provider_ref}:{model_ref}", "req_per_day", 86400),
    ]:
        limit = limits.get(limit_key)
        if limit is None:
            continue
        try:
            rl.check(key, limit=limit, window=window, weight=0)
        except RateLimitExceeded as e:
            info["ok"] = False
            info[f"error_{limit_key}"] = str(e)
            continue
        budget = limit - sum(w for ts, w in rl._buckets.get(key, [])
                            if ts > now - window)

    return info


def record_usage(provider_ref: str, model_ref: str,
                 tokens: int = 0, requests: int = 1) -> dict:
    """Enregistre la consommation et retourne le budget restant."""
    rl = _get_rl()
    prefix = f"{provider_ref}:{model_ref}"

    if tokens:
        rl.record(f"t:m:{prefix}", window=60, weight=tokens)
        rl.record(f"t:d:{prefix}", window=86400, weight=tokens)
    if requests:
        rl.record(f"r:m:{prefix}", window=60, weight=requests)
        rl.record(f"r:d:{prefix}", window=86400, weight=requests)

    limits = _get().effective_limits(provider_ref, model_ref)
    if limits.get("unlimited"):
        return {"remaining_tokens_min": None, "remaining_tokens_day": None}

    now = time.monotonic()
    remaining = {}
    for key, limit_key, window in [
        ("t:m", "tokens_per_min", 60),
        ("t:d", "tokens_per_day", 86400),
        ("r:m", "req_per_min", 60),
        ("r:d", "req_per_day", 86400),
    ]:
        limit = limits.get(limit_key)
        if limit is None:
            continue
        used = sum(w for ts, w in rl._buckets.get(f"{key}:{prefix}", [])
                   if ts > now - window)
        remaining[f"remaining_{key.replace(':','_')}"] = limit - used

    return remaining
