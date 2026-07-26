"""Scraper Artificial Analysis.

Source : https://artificialanalysis.ai/

Fournit qualité, vitesse (tokens/sec), latence (TTFT),
et prix par million de tokens pour chaque modèle.

Utile pour les stratégies eco (prix) et fast (vitesse).
"""

from __future__ import annotations
from typing import Any, Dict, List
import json
import urllib.request
import urllib.error
import ssl


API_URL = "https://artificialanalysis.ai/api/models"


def _normalize_model_name(name: str) -> str:
    """Normalise les noms Artificial Analysis vers notre format canonique.

    Artificial Analysis utilise des noms comme :
        'GPT-4o', 'Claude 3.5 Sonnet', 'Gemini 2.0 Flash'
    """
    n = name.strip()

    provider_map = [
        (lambda s: s.startswith("GPT") or s.startswith("O1") or s.startswith("O3"),
         "openai/"),
        (lambda s: s.startswith("Claude"), "anthropic/"),
        (lambda s: s.startswith("Gemini") or s.startswith("Gemma"), "google/"),
        (lambda s: s.startswith("Llama"), "meta/"),
        (lambda s: s.startswith("Mistral") or s.startswith("Mixtral"), "mistral/"),
        (lambda s: s.startswith("DeepSeek"), "deepseek/"),
        (lambda s: s.startswith("Qwen"), "qwen/"),
        (lambda s: s.startswith("Command"), "cohere/"),
        (lambda s: s.startswith("DBRX"), "databricks/"),
    ]

    for matcher, prefix in provider_map:
        if matcher(n):
            n = prefix + n
            break

    ref = n.lower().replace(" ", "-").replace(".", "")
    # Nettoyer suffixes de version
    for suffix in ["-2024", "-2025", "-2026", "-0314", "-0513", "-0718",
                   "-0828", "-1106", "-1219", "-0104", "-0125",
                   "-exp", "-beta", "-latest", "-turbo"]:
        idx = ref.rfind(suffix)
        if idx > 0 and idx + len(suffix) == len(ref):
            ref = ref[:idx]

    return ref


def fetch() -> List[Dict[str, Any]]:
    """Récupère les données qualité/prix/vitesse depuis Artificial Analysis.

    Retourne une liste de dicts :
        model_ref     : str
        benchmark_key : str ('artificial_analysis')
        metric_name   : str ('quality', 'speed_tps', 'cost_per_m_input', 'cost_per_m_output')
        raw_value     : float
        source_url    : str
    """
    ctx = ssl.create_default_context()
    req = urllib.request.Request(
        API_URL,
        headers={"User-Agent": "ModelWeaver/0.8.5", "Accept": "application/json"},
    )

    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            data = json.loads(resp.read().decode())
    except (urllib.error.URLError, json.JSONDecodeError, OSError) as e:
        print(f"    [artificial_analysis] Failed to fetch from {API_URL}: {e}")
        return []

    if isinstance(data, dict):
        models = data.get("models", data.get("data", []))
    elif isinstance(data, list):
        models = data
    else:
        return []

    results = []
    seen = set()

    for item in models:
        if isinstance(item, dict):
            name = item.get("name", item.get("model", ""))
        else:
            continue

        if not name:
            continue

        model_ref = _normalize_model_name(str(name))
        if not model_ref or model_ref in seen:
            continue
        seen.add(model_ref)

        # Qualité (0-100)
        quality = item.get("quality", item.get("overall_quality"))
        if quality is not None:
            results.append({
                "model_ref": model_ref,
                "benchmark_key": "artificial_analysis",
                "metric_name": "quality",
                "raw_value": float(quality),
                "source_url": API_URL,
            })

        # Vitesse (tokens/sec)
        speed = item.get("speed", item.get("tokens_per_second", item.get("output_speed")))
        if speed is not None:
            results.append({
                "model_ref": model_ref,
                "benchmark_key": "artificial_analysis",
                "metric_name": "speed_tps",
                "raw_value": float(speed),
                "source_url": API_URL,
            })

        # Prix input ($/M tokens)
        cost_in = item.get("price_per_million_input_tokens",
                          item.get("input_price", item.get("cost_per_m_input")))
        if cost_in is not None:
            results.append({
                "model_ref": model_ref,
                "benchmark_key": "artificial_analysis",
                "metric_name": "cost_per_m_input",
                "raw_value": float(cost_in),
                "source_url": API_URL,
            })

        # Prix output ($/M tokens)
        cost_out = item.get("price_per_million_output_tokens",
                           item.get("output_price", item.get("cost_per_m_output")))
        if cost_out is not None:
            results.append({
                "model_ref": model_ref,
                "benchmark_key": "artificial_analysis",
                "metric_name": "cost_per_m_output",
                "raw_value": float(cost_out),
                "source_url": API_URL,
            })

    return results
