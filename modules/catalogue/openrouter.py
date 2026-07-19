"""Synchronisation des tarifs depuis OpenRouter (api/v1/models).

OpenRouter liste ~338 modeles avec tarifs par token (prompt/completion) et
context_length, couvrant de nombreux providers que litellm ne tarife quasiment
pas (openai 67, google 28, anthropic 15, mistralai, meta-llama, x-ai, qwen...).

Les modeles dont l'id se termine par ':free' (ou a un cout nul) sont marques
free-tier. Les providers absents du catalogue sont crees a la volee.

Utilisation :
    from modules.catalogue.openrouter import fetch_openrouter_models, merge_openrouter
    data = fetch_openrouter_models()
    merge_openrouter(CatalogueDB(), data)
"""

import json
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional

from modules.sql.db import CatalogueDB

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
CACHE_DIR = REPO_ROOT / ".modelweaver" / "cache" / "pricing"

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
OPENROUTER_ALIAS_TARGET = "openrouter"
OPENROUTER_ALIAS_SOURCE = "openrouter-api"

CACHE_FILE = CACHE_DIR / "openrouter_models.json"

# Provider OpenRouter -> ref provider catalogue (reconciliation explicite).
# La plupart des ids OpenRouter sont deja au format provider catalogue ;
# cette table ne sert qu'aux exceptions.
OPENROUTER_PROVIDER_ALIASES = {
    "meta-llama": "meta-llama",
    "x-ai": "x-ai",
    "mistralai": "mistralai",
    "qwen": "qwen",
}

# Couts OpenRouter = USD par token ; litellm/catalogue utilise le meme format
# (cost_per_*_token en notation scientifique texte, ex '5e-08').


def _ensure_provider(cat: CatalogueDB, ref: str) -> int:
    row = cat.conn.execute(
        "SELECT id FROM catalogue_providers WHERE ref=?", (ref,)).fetchone()
    if row:
        return row["id"]
    cur = cat.conn.execute(
        "INSERT INTO catalogue_providers (ref, name, provider_type) VALUES (?,?, 'cloud')",
        (ref, ref))
    return cur.lastrowid


def _ensure_model(cat: CatalogueDB, ref: str, developer: str) -> int:
    row = cat.conn.execute(
        "SELECT id FROM catalogue_models WHERE ref=?", (ref,)).fetchone()
    if row:
        return row["id"]
    cur = cat.conn.execute(
        "INSERT INTO catalogue_models (ref, name, developer) VALUES (?,?,?)",
        (ref, ref, developer))
    return cur.lastrowid


def fetch_openrouter_models(force: bool = False,
                            api_key: Optional[str] = None) -> Dict[str, Any]:
    """Telecharge (avec cache disque) la liste des modeles OpenRouter.

    Retourne un dict {model_id: {pricing, context_length, per_request_limits,
    name, provider, is_free, ...}}.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    if not force and CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    import os
    key = api_key or os.environ.get("OPENROUTER_API_KEY")
    req = urllib.request.Request(
        OPENROUTER_MODELS_URL,
        headers={"Authorization": f"Bearer {key}"} if key else {})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = json.loads(resp.read().decode())
    models = {}
    for m in raw.get("data", []):
        mid = m.get("id", "")
        if not mid:
            continue
        pricing = m.get("pricing", {}) or {}
        try:
            pin = float(pricing.get("prompt", "0") or 0)
        except (TypeError, ValueError):
            pin = 0.0
        try:
            pout = float(pricing.get("completion", "0") or 0)
        except (TypeError, ValueError):
            pout = 0.0
        is_free = (
            mid.endswith(":free")
            or (pin == 0.0 and pout == 0.0)
            or pricing.get("prompt") in ("-1",)
            or pricing.get("completion") in ("-1",)
        )
        models[mid] = {
            "id": mid,
            "name": m.get("name", mid),
            "provider": mid.split("/")[0] if "/" in mid else "",
            "cost_per_input_token": f"{pin:.6e}" if pin > 0 else None,
            "cost_per_output_token": f"{pout:.6e}" if pout > 0 else None,
            "context_window_tokens": m.get("context_length"),
            "max_output_tokens": None,
            "per_request_limits": m.get("per_request_limits"),
            "is_free": is_free,
        }
    CACHE_FILE.write_text(json.dumps(models, indent=2), encoding="utf-8")
    return models


def merge_openrouter(cat: CatalogueDB, data: Dict[str, Any],
                     dry_run: bool = False,
                     only_free_tier: bool = False) -> Dict[str, int]:
    """Fusionne les tarifs OpenRouter dans provider_models.

    Cree les providers/models absents, applique les couts + context_window,
    marque free_tier. Retourne des compteurs.
    """
    # index catalogue par (provider_ref, model_ref) pour le matching
    by_ref = {}
    for r in cat.conn.execute(
        "SELECT pm.id, p.ref AS pref, m.ref AS mref, "
        "pm.context_window_tokens, pm.cost_per_input_token, pm.free_tier "
        "FROM provider_models pm "
        "JOIN catalogue_providers p ON p.id=pm.provider_id "
        "JOIN catalogue_models m ON m.id=pm.model_id"
    ).fetchall():
        by_ref[(r["pref"], r["mref"])] = r

    updates, creates = [], []
    stats = {"matched": 0, "updated": 0, "created": 0,
             "free_tier": 0, "skipped": 0, "providers_created": 0}

    # providers vus pour ne pas recompter
    seen_prov = set()

    for mid, entry in data.items():
        prov = entry["provider"]
        model = mid.split("/", 1)[-1]
        if not prov or not model:
            stats["skipped"] += 1
            continue
        if only_free_tier and not entry["is_free"]:
            continue
        # reconciliation provider explicite
        pref = OPENROUTER_PROVIDER_ALIASES.get(prov, prov)
        is_free = entry["is_free"]
        stats["matched"] += 1
        if is_free:
            stats["free_tier"] += 1

        if (pref, model) in by_ref:
            r = by_ref[(pref, model)]
            extra = {}
            if r["context_window_tokens"] is None and entry["context_window_tokens"] is not None:
                extra["context_window_tokens"] = entry["context_window_tokens"]
            if r["cost_per_input_token"] is None and entry["cost_per_input_token"] is not None:
                extra["cost_per_input_token"] = entry["cost_per_input_token"]
                extra["cost_per_output_token"] = entry["cost_per_output_token"]
            extra["free_tier"] = 1 if is_free else 0
            updates.append((r["id"], extra))
            stats["updated"] += 1
        else:
            if pref not in seen_prov:
                if dry_run:
                    pass
                else:
                    _ensure_provider(cat, pref)
                seen_prov.add(pref)
                stats["providers_created"] += 1
            creates.append((pref, model, entry))
            stats["created"] += 1

    if not dry_run:
        for pm_id, extra in updates:
            sets, vals = [], []
            for col in ("cost_per_input_token", "cost_per_output_token",
                        "context_window_tokens", "free_tier"):
                if col in extra:
                    sets.append(f"{col}=?")
                    vals.append(extra[col])
            if sets:
                vals.append(pm_id)
                cat.conn.execute(
                    f"UPDATE provider_models SET {', '.join(sets)} WHERE id=?", vals)
        for pref, model, entry in creates:
            pid = _ensure_provider(cat, pref)
            mid = _ensure_model(cat, model, pref)
            cat.conn.execute(
                """INSERT OR IGNORE INTO provider_models
                   (provider_id, model_id, provider_model_name,
                    context_window_tokens, cost_per_input_token,
                    cost_per_output_token, free_tier)
                   VALUES (?,?,?,?,?,?,?)""",
                (pid, mid, f"{pref}/{model}",
                 entry["context_window_tokens"],
                 entry["cost_per_input_token"], entry["cost_per_output_token"],
                 1 if entry["is_free"] else 0))
        cat.conn.commit()
    return stats
