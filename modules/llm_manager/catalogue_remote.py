"""Catalogue Remote — synchronisation depuis un endpoint central.

Interroge un endpoint HTTP unique qui retourne tout le catalogue
(providers + modèles + capacités + coûts), le met en cache local,
et permet un refresh silencieux en arrière-plan.

Structure attendue du endpoint :

    {
      "openai": {
        "name": "OpenAI",
        "env": ["OPENAI_API_KEY"],
        "models": {
          "gpt-4o": {
            "name": "GPT-4o",
            "cost": {"input": 2.5, "output": 10,
                     "cache_read": 1.25, "cache_write": 2.5},
            "limit": {"context": 128000, "output": 16384},
            "capabilities": {"chat": true, "tool_call": true,
                             "attachment": true, "reasoning": false},
            "release_date": "2024-05-13",
            "status": "active"
          }
        }
      },
      "anthropic": { ... }
    }

Inspiré de : opencode packages/core/src/models-dev.ts
"""

import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from services._common import mw_home

logger = logging.getLogger("modelweaver.catalogue_remote")

_MW_HOME = mw_home()
_CACHE_DIR = _MW_HOME / "cache"
_CACHE_FILE = _CACHE_DIR / "catalogue.json"

_CATALOGUE_URL = os.environ.get(
    "MODELWEAVER_CATALOGUE_URL",
    "https://models.modelweaver.dev/api.json",
)
_CATALOGUE_TTL = int(os.environ.get("MODELWEAVER_CATALOGUE_TTL", "300"))
_REFRESH_INTERVAL = int(os.environ.get("MODELWEAVER_CATALOGUE_REFRESH", "3600"))
_USER_AGENT = "ModelWeaver/1.0"


def _cache_valid(path: Path, ttl: int) -> bool:
    try:
        mtime = path.stat().st_mtime
        return (time.time() - mtime) < ttl
    except OSError:
        return False


def _read_cache(path: Path) -> Optional[Dict[str, Any]]:
    try:
        raw = path.read_text()
        data: dict = json.loads(raw)
        data.setdefault("_meta", {})["_from_cache"] = True
        return data
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".{os.getpid()}.{int(time.time() * 1000)}.tmp")
    try:
        tmp.write_text(json.dumps(data, indent=2))
        tmp.rename(path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _fetch_remote(url: str, timeout: int = 10) -> Optional[str]:
    try:
        req = Request(url, headers={"User-Agent": _USER_AGENT})
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode()
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        logger.debug("Fetch %s failed: %s", url, exc)
        return None


def fetch(force: bool = False, url: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Retourne le catalogue depuis le cache local ou l'endpoint distant.

    Args:
        force: Ignore le TTL et force un fetch distant.
        url: Surcharge l'URL du catalogue (utile pour les miroirs).

    Returns:
        Le catalogue complet ou ``None`` si indisponible.
    """
    path = _CACHE_FILE
    target_url = url or _CATALOGUE_URL

    if not force and _cache_valid(path, _CATALOGUE_TTL):
        cached = _read_cache(path)
        if cached is not None:
            return cached

    raw = _fetch_remote(target_url)
    if raw is not None:
        try:
            data: dict = json.loads(raw)
            data["_meta"] = {
                "_fetched_at": time.time(),
                "_url": target_url,
                "_from_cache": False,
            }
            _write_cache(path, data)
            return data
        except json.JSONDecodeError:
            logger.warning("Invalid JSON from catalogue endpoint")

    cached = _read_cache(path)
    if cached is not None:
        logger.info("Catalogue fetch failed, using stale cache")
        return cached

    logger.error("No catalogue available (network + cache miss)")
    return None


def refresh_sync(cat, km=None, force: bool = False) -> int:
    """Point d'entrée pour la synchronisation : remplit la DB depuis le remote.

    Appelée par le scheduler ou manuellement. Retourne le nombre de modèles
    synchronisés.

    Compatible avec l'interface existante ``run_sync()`` de catalogue_sync.
    """
    data = fetch(force=force)
    if data is None:
        return 0

    count = 0
    for provider_ref, pdata in data.items():
        if provider_ref.startswith("_"):
            continue
        _upsert_provider(cat, provider_ref, pdata)
        for model_id, mdata in pdata.get("models", {}).items():
            _upsert_model(cat, provider_ref, model_id, mdata)
            _upsert_provider_model(cat, provider_ref, model_id, mdata)
            count += 1
    cat.conn.commit()
    logger.info("Catalogue sync: %d modeles depuis %s", count, data.get("_meta", {}).get("_url", "?"))
    return count


def _upsert_provider(cat, ref: str, pdata: dict) -> None:
    cat.conn.execute("""
        INSERT INTO catalogue_providers (ref, name, provider_type, api_type)
        VALUES (?, ?, 'cloud', 'openai-compatible')
        ON CONFLICT(ref) DO UPDATE SET
            name = COALESCE(NULLIF(EXCLUDED.name, ''), catalogue_providers.name)
    """, (ref, pdata.get("name", ref)))


def _upsert_model(cat, provider_ref: str, model_id: str, mdata: dict) -> None:
    name = mdata.get("name", model_id)
    cat.conn.execute("""
        INSERT INTO catalogue_models (ref, name, release_year)
        VALUES (?, ?, ?)
        ON CONFLICT(ref) DO UPDATE SET
            name = COALESCE(NULLIF(EXCLUDED.name, ''), catalogue_models.name)
    """, (model_id, name, mdata.get("release_date", "")[:4]))


def _upsert_provider_model(cat, provider_ref: str, model_id: str, mdata: dict) -> None:
    caps = mdata.get("capabilities", {})
    cost = mdata.get("cost", {})
    limit = mdata.get("limit", {})

    cat.conn.execute("""
        INSERT INTO provider_models
            (provider_id, model_id, provider_model_name,
             context_window_tokens, max_output_tokens,
             cost_per_input_token, cost_per_output_token, status)
        VALUES (
            (SELECT id FROM catalogue_providers WHERE ref = ?),
            (SELECT id FROM catalogue_models WHERE ref = ?),
            ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT DO NOTHING
    """, (
        provider_ref, model_id, model_id,
        limit.get("context"), limit.get("output"),
        cost.get("input"), cost.get("output"),
        mdata.get("status", "active"),
    ))

    cat.conn.execute("""
        INSERT INTO model_capabilities
            (model_ref, supports_chat, supports_function_calling,
             supports_vision, supports_embedding, supports_streaming, source)
        VALUES (?, ?, ?, ?, ?, ?, 'remote')
        ON CONFLICT(model_ref) DO UPDATE SET
            supports_chat = COALESCE(EXCLUDED.supports_chat, model_capabilities.supports_chat),
            supports_function_calling = COALESCE(EXCLUDED.supports_function_calling, model_capabilities.supports_function_calling),
            supports_vision = COALESCE(EXCLUDED.supports_vision, model_capabilities.supports_vision),
            source = CASE WHEN model_capabilities.source = 'unknown' THEN 'remote' ELSE model_capabilities.source END
    """, (
        model_id,
        int(caps.get("chat", caps.get("supports_chat", False))),
        int(caps.get("tool_call", caps.get("supports_function_calling", False))),
        int(caps.get("attachment", caps.get("supports_vision", False))),
        0, 1,  # embedding, streaming
    ))

    cat.conn.execute("""
        INSERT OR IGNORE INTO provider_models_mapping
            (provider_id, model_id, provider_model_name, declared, available, last_checked_at)
        VALUES (
            (SELECT id FROM catalogue_providers WHERE ref = ?),
            (SELECT id FROM catalogue_models WHERE ref = ?),
            ?, 1, 1, strftime('%s','now')
        )
    """, (provider_ref, model_id, model_id))
