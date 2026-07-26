"""Données benchmark initiales — saisies manuellement pour les modèles qu'on utilise.

Sera remplacé par du scraping live quand les APIs seront stables.
Sources : LMSYS Arena Elo (Juillet 2026), Artificial Analysis, SWE-bench Verified.
"""

from __future__ import annotations
from typing import Any, Dict, List


# (model_ref, elo, quality%, speed_tps, cost_per_m_input, cost_per_m_output, swe_bench)
# quality% = Artificial Analysis Quality Index (0-100)
# elo = LMSYS Arena Elo score
_SEED_DATA = [
    # OpenAI
    ("openai/gpt-4o",              1365, 92,  85,  2.50,  10.00, 48.0),
    ("openai/gpt-4o-mini",          1320, 85, 120,  0.15,   0.60, 32.0),
    ("openai/o1",                   1380, 95,  30, 15.00,  60.00, 62.0),
    ("openai/o3-mini",              1340, 90,  55,  1.10,   4.40, 55.0),

    # Anthropic
    ("anthropic/claude-3-5-sonnet", 1370, 93,  55,  3.00,  15.00, 50.0),
    ("anthropic/claude-3-5-haiku",  1310, 83,  95,  0.80,   4.00, 35.0),
    ("anthropic/claude-4-sonnet",   1390, 96,  50,  3.50,  17.50, 58.0),

    # Google
    ("google/gemini-2.0-flash",     1330, 87, 110,  0.10,   0.40, 30.0),
    ("google/gemini-2.5-flash",     1345, 89, 100,  0.15,   0.60, 36.0),
    ("google/gemini-2.0-pro",       1350, 91,  60,  0.50,   1.50, 42.0),

    # Meta
    ("meta/llama-3.1-70b",          1280, 80,  45,  0.59,   0.79, 28.0),
    ("meta/llama-3.1-405b",         1300, 85,  30,  2.00,   2.00, 35.0),
    ("meta/llama-4-70b",            1315, 86,  50,  0.70,   0.90, 38.0),

    # Mistral
    ("mistral/mistral-large",       1290, 82,  70,  2.00,   6.00, 30.0),
    ("mistral/mistral-small",       1260, 76,  90,  0.20,   0.60, 22.0),

    # DeepSeek
    ("deepseek/deepseek-chat",      1340, 88,  75,  0.14,   0.28, 42.0),
    ("deepseek/deepseek-reasoner",  1355, 91,  40,  0.55,   2.19, 48.0),

    # Cohere
    ("cohere/command-r-plus",       1240, 72,  65,  2.50,  10.00, 18.0),

    # Qwen
    ("qwen/qwen3-72b",              1310, 84,  55,  0.35,   0.70, 34.0),
    ("qwen/qwen3-32b",              1295, 81,  70,  0.20,   0.40, 28.0),
]


def fetch() -> List[Dict[str, Any]]:
    """Retourne les données benchmark pré-remplies."""
    results = []

    for (ref, elo, quality, speed, cost_in, cost_out, swe) in _SEED_DATA:
        results.append({"model_ref": ref, "benchmark_key": "lmsys_arena_elo",
                        "metric_name": "elo", "raw_value": elo,
                        "source_url": "seeded/lmsys"})
        results.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                        "metric_name": "quality", "raw_value": quality,
                        "source_url": "seeded/artificial-analysis"})
        results.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                        "metric_name": "speed_tps", "raw_value": speed,
                        "source_url": "seeded/artificial-analysis"})
        results.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                        "metric_name": "cost_per_m_input", "raw_value": cost_in,
                        "source_url": "seeded/artificial-analysis"})
        results.append({"model_ref": ref, "benchmark_key": "artificial_analysis",
                        "metric_name": "cost_per_m_output", "raw_value": cost_out,
                        "source_url": "seeded/artificial-analysis"})
        results.append({"model_ref": ref, "benchmark_key": "swe_bench_verified",
                        "metric_name": "pass_rate", "raw_value": swe,
                        "source_url": "seeded/swe-bench"})

    return results
