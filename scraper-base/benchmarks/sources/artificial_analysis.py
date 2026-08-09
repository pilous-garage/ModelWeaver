"""Scraper Artificial Analysis — API v2 (authentifiée).

Source : https://artificialanalysis.ai/data-api/docs

Endpoint free-tier : GET https://artificialanalysis.ai/api/v2/language/models/free
Auth : header ``x-api-key: <AA_API_KEY>`` (clé lue depuis l'env / .env,
  JAMAIS en clair dans le code). Rate limit free : 100 requêtes / 24h
  (fenêtre fixe). Pagination 1-indexée (page_size 200).

Fournit par modèle :
  - artificial_analysis_intelligence_index → quality_pct (0-100)
  - artificial_analysis_coding_index       → pass_rate (coding/reasoning)
  - artificial_analysis_agentic_index      → agentic
  - price_1m_input_tokens / price_1m_output_tokens → coût
  - median_output_tokens_per_second        → speed_tps
  - median_end_to_end_response_time_seconds → latence (ms)

Toutes les lignes sont marquées is_synthetic=0 (données RÉELLES mesurées).
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
import ssl
from typing import Any, Dict, List

API_BASE = "https://artificialanalysis.ai/api/v2"
MODELS_FREE_URL = API_BASE + "/language/models/free"
PAGE_SIZE = 200


def _api_key() -> str:
    """Clé API Artificial Analysis.

    Priorité :
      1. KeyManager (keyring OS, provider_ref 'artificial_analysis') — la
         source de vérité : la clé vit chiffrée dans le keyring, jamais en dur.
      2. env AA_API_KEY / .env racine (fallback headless).
    """
    try:
        from modules.key_manager.key_manager_module import KeyManager
        km = KeyManager()
        key = km.get_key("artificial_analysis")
        if key and key.get("api_key"):
            return key["api_key"].strip()
    except Exception:
        pass

    key = os.getenv("AA_API_KEY", "").strip()
    if not key:
        # Charger depuis .env racine (comme consolidate._load_env)
        try:
            from pathlib import Path
            env = Path(__file__).resolve().parent.parent.parent.parent / ".env"
            if env.exists():
                for line in env.read_text().splitlines():
                    line = line.strip()
                    if line.startswith("AA_API_KEY="):
                        key = line.partition("=")[2].strip()
                        break
        except Exception:
            pass
    return key


def _fetch_page(page: int) -> Dict[str, Any]:
    """Récupère une page de /language/models/free. Lève sur erreur HTTP."""
    key = _api_key()
    if not key:
        raise RuntimeError("AA_API_KEY manquante (env ou .env)")

    ctx = ssl.create_default_context()
    url = f"{MODELS_FREE_URL}?page={page}"
    req = urllib.request.Request(
        url,
        headers={
            "x-api-key": key,
            "User-Agent": "ModelWeaver/0.8.5",
            "Accept": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        if e.code == 401:
            raise RuntimeError("artificial_analysis: 401 clé API invalide")
        if e.code == 403:
            raise RuntimeError("artificial_analysis: 403 tier ne couvre pas /language/models/free")
        if e.code == 429:
            retry = resp_headers.get("Retry-After") if (resp_headers := getattr(e, "headers", None)) else None
            raise RuntimeError(f"artificial_analysis: 429 rate limit dépassé (Retry-After={retry})")
        if e.code == 404:
            raise RuntimeError(f"artificial_analysis: 404 endpoint introuvable ({url})")
        raise RuntimeError(f"artificial_analysis: HTTP {e.code} {body}")
    except urllib.error.URLError as e:
        raise RuntimeError(f"artificial_analysis: erreur réseau {e}")


def _normalize_model_name(name: str) -> str:
    """Normalise le slug AA vers notre format canonique (lowercase, traits)."""
    n = (name or "").strip().lower()
    n = n.replace("_", "-")
    n = " ".join(n.split())
    n = n.replace(" ", "-")
    return n.strip("-")


def _float(v: Any, scale: float = 1.0) -> float:
    """Convertit un champ AA (None = non mesuré) en float, None si absent."""
    if v is None:
        return 0.0
    try:
        return float(v) * scale
    except (TypeError, ValueError):
        return 0.0


def fetch() -> List[Dict[str, Any]]:
    """Récupère tous les modèles langage (free tier, paginé).

    Retourne les lignes normalisées (même format que lmsys/arena_hard) avec
    is_synthetic=0 (données réelles) et confidence=1.0.
    """
    rows: List[Dict[str, Any]] = []
    page = 1
    total_pages = 1

    while True:
        try:
            data = _fetch_page(page)
        except RuntimeError as e:
            print(f"    [artificial_analysis] {e}")
            return rows if rows else []

        pag = data.get("pagination", {})
        total_pages = pag.get("total_pages", 1)
        items = data.get("data", []) or []

        for m in items:
            slug = m.get("slug", "")
            name = m.get("name", "")
            if not slug:
                continue
            ref = _normalize_model_name(slug)
            if not ref:
                continue

            ev = m.get("evaluations", {}) or {}
            pricing = m.get("pricing", {}) or {}
            perf = m.get("performance", {}) or {}
            creator = m.get("model_creator", {}) or {}
            creator_name = creator.get("name", "")
            release_date = m.get("release_date")

            # Métadonnées communes à chaque ligne (release_date, éditeur).
            meta = {"creator": creator_name}
            if release_date:
                meta["release_date"] = release_date
            meta_json = json.dumps(meta, ensure_ascii=False)

            # Qualité globale (Intelligence Index)
            quality = ev.get("artificial_analysis_intelligence_index")
            if quality is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "quality_pct",
                    "raw_value": _float(quality),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Coding / reasoning (Coding Index)
            coding = ev.get("artificial_analysis_coding_index")
            if coding is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "pass_rate",
                    "raw_value": _float(coding),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Agentic (Agentic Index)
            agentic = ev.get("artificial_analysis_agentic_index")
            if agentic is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "agentic_index",
                    "raw_value": _float(agentic),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Vitesse
            speed = perf.get("median_output_tokens_per_second")
            if speed is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "speed_tps",
                    "raw_value": _float(speed),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Latence E2E (s) → ms
            lat = perf.get("median_end_to_end_response_time_seconds")
            if lat is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "latency_ms",
                    "raw_value": _float(lat, 1000.0),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Time To First Token (s) → ms : latence PERÇUE avant le 1er token
            # (plus pertinente pour la réactivité que la latence E2E).
            ttft = perf.get("median_time_to_first_token_seconds")
            if ttft is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "ttft_ms",
                    "raw_value": _float(ttft, 1000.0),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Time To First Answer Token (s) → ms
            tta = perf.get("median_time_to_first_answer_token_seconds")
            if tta is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "ttft_answer_ms",
                    "raw_value": _float(tta, 1000.0),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            # Coût (input / output, $/1M tokens)
            cost_in = pricing.get("price_1m_input_tokens")
            cost_out = pricing.get("price_1m_output_tokens")
            if cost_in is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "cost_per_m_input",
                    "raw_value": _float(cost_in),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })
            if cost_out is not None:
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "artificial_analysis",
                    "metric_name": "cost_per_m_output",
                    "raw_value": _float(cost_out),
                    "source_url": MODELS_FREE_URL,
                    "is_synthetic": 0,
                    "confidence": 1.0,
                    "meta_json": meta_json,
                })

        if page >= total_pages:
            break
        page += 1
        time.sleep(0.3)  # respect du rate-limit free (100 req/24h)

    return rows


if __name__ == "__main__":
    result = fetch()
    print(f"artificial_analysis: {len(result)} lignes réelles")
    models = sorted({r['model_ref'] for r in result})
    print(f"modèles distincts: {len(models)}")
    for ref in models[:15]:
        print("  ", ref)
