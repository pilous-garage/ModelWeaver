"""Scraper LMSYS Chatbot Arena Leaderboard.

Source : Hugging Face dataset lmsys/lmsys-arena-elo-results-v2
         ou CSV direct depuis le HF Space.

Score Elo = qualité perçue par des humains en aveugle.
C'est le benchmark de référence pour la qualité "réelle".
"""

from __future__ import annotations
from typing import Any, Dict, List
import json
import urllib.request
import urllib.error
import ssl


LEADERBOARD_JSON = "https://huggingface.co/spaces/lmarena-ai/arena-leaderboard/raw/main/leaderboard_table.json"


def _normalize_model_name(name: str) -> str:
    """Normalise les noms de modèles LMSYS vers notre format canonique.

    Exemples :
        'gpt-4o-2024-05-13' -> 'openai/gpt-4o'
        'gpt-4o-mini-2024-07-18' -> 'openai/gpt-4o-mini'
        'claude-3-5-sonnet-20241022' -> 'anthropic/claude-3-5-sonnet'
        'gemini-2.0-flash-exp' -> 'google/gemini-2.0-flash'
        'Meta-Llama-3-70B-Instruct' -> 'meta/llama-3-70b-instruct'
    """
    n = name.strip().lower()

    provider_map = [
        ("gpt-", "openai/gpt-"),
        ("o1-", "openai/o1-"),
        ("o3-", "openai/o3-"),
        ("claude", "anthropic/claude"),
        ("gemini", "google/gemini"),
        ("gemma", "google/gemma"),
        ("meta-llama", "meta/llama"),
        ("llama", "meta/llama"),
        ("mistral", "mistral/mistral"),
        ("mixtral", "mistral/mixtral"),
        ("deepseek", "deepseek/deepseek"),
        ("qwen", "qwen/qwen"),
        ("command", "cohere/command"),
        ("dbrx", "databricks/dbrx"),
    ]

    for prefix, replacement in provider_map:
        if n.startswith(prefix):
            n = replacement + n[len(prefix):]
            break

    # Nettoyer suffixes de version (formats: -YYYY, -YYYYMMDD, -YYYY-MM-DD, -vN, etc.)
    import re
    n = re.sub(r'-\d{4}\d{2}\d{2}$', '', n)     # -YYYYMMDD (8 digits)
    n = re.sub(r'-\d{4}(-\d{2}(-\d{2})?)?$', '', n)  # -YYYY, -YYYY-MM, -YYYY-MM-DD
    n = re.sub(r'-v\d+$', '', n)
    n = re.sub(r'-(exp|beta|latest|turbo|snapshot)$', '', n)

    return n


def fetch() -> List[Dict[str, Any]]:
    """Récupère les scores Elo depuis le leaderboard LMSYS.

    Retourne une liste de dicts :
        model_ref     : str (canonique, ex: 'openai/gpt-4o')
        benchmark_key : str ('lmsys_arena_elo')
        metric_name   : str ('elo')
        raw_value     : float
        source_url    : str
    """
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        LEADERBOARD_JSON,
        headers={"User-Agent": "ModelWeaver/0.8.5"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"    [lmsys] Failed to fetch from {LEADERBOARD_JSON}: {e}")
        return []

    # Le JSON peut être une liste de modèles ou un dict avec clé 'models'
    if isinstance(data, dict):
        rows = data.get("models", data.get("data", []))
    else:
        rows = data

    results = []
    seen = set()

    for item in rows:
        if isinstance(item, dict):
            name = item.get("name", item.get("model", ""))
            elo = item.get("elo", item.get("score", item.get("gpt_score", 0)))
        elif isinstance(item, list) and len(item) >= 2:
            name = item[0]
            elo = item[1]
        else:
            continue

        if not name or not elo:
            continue

        model_ref = _normalize_model_name(str(name))
        if not model_ref or model_ref in seen:
            continue
        seen.add(model_ref)

        results.append({
            "model_ref": model_ref,
            "benchmark_key": "lmsys_arena_elo",
            "metric_name": "elo",
            "raw_value": float(elo),
            "source_url": LEADERBOARD_JSON,
        })

    return results
