"""Frontier model benchmark data — known/estimated scores for the very latest models.

As of mid-2026, many frontier models are too new to appear in LMSYS/Arena-Hard CSVs.
This source provides estimated benchmark rows based on published performance summaries,
model cards, and provider announcements.

Every row is marked ``is_synthetic = 1`` with ``confidence = 0.6`` (better than the
generic catalogue estimate of 0.3) so downstream consumers know these are informed
estimates rather than pure guesses.

Method:
  - Scores are assigned per model based on available public benchmarks and
    provider-reported capabilities.
  - Rows use the same metric names as LMSYS/Arena-Hard so they feed naturally
    into the task-type breakdown (score_chat, score_knowledge, score_coding,
    score_reasoning, score_agentic).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


FRONTIER_MODELS: Dict[str, Dict[str, Any]] = {
    # ── OpenAI ──
    "gpt-5.6-luna-pro": {
        "quality_pct": 97.0, "mt_bench_quality": 9.5, "mmlu_knowledge": 89.0,
        "score": 96.0, "pass_rate": 96.0, "speed_tps": 62.0,
        "cost_per_m_input": 25.0, "cost_per_m_output": 100.0,
        "reliability_pct": 95.0, "source_url": "frontier/openai-estimate",
    },
    "gpt-5.6-terra-pro": {
        "quality_pct": 95.0, "mt_bench_quality": 9.3, "mmlu_knowledge": 87.0,
        "score": 93.0, "pass_rate": 93.0, "speed_tps": 65.0,
        "cost_per_m_input": 20.0, "cost_per_m_output": 80.0,
        "reliability_pct": 94.0, "source_url": "frontier/openai-estimate",
    },
    "gpt-5.6-sol-pro": {
        "quality_pct": 94.0, "mt_bench_quality": 9.1, "mmlu_knowledge": 86.0,
        "score": 91.0, "pass_rate": 91.0, "speed_tps": 70.0,
        "cost_per_m_input": 15.0, "cost_per_m_output": 60.0,
        "reliability_pct": 93.0, "source_url": "frontier/openai-estimate",
    },
    # ── Anthropic ──
    "claude-opus-4.8": {
        "quality_pct": 96.0, "mt_bench_quality": 9.4, "mmlu_knowledge": 88.0,
        "score": 95.0, "pass_rate": 95.0, "speed_tps": 55.0,
        "cost_per_m_input": 15.0, "cost_per_m_output": 75.0,
        "reliability_pct": 97.0, "source_url": "frontier/anthropic-estimate",
    },
    "claude-opus-4.8-fast": {
        "quality_pct": 91.0, "mt_bench_quality": 8.9, "mmlu_knowledge": 82.0,
        "score": 89.0, "pass_rate": 89.0, "speed_tps": 68.0,
        "cost_per_m_input": 5.0, "cost_per_m_output": 20.0,
        "reliability_pct": 95.0, "source_url": "frontier/anthropic-estimate",
    },
    "claude-opus-4.7-fast": {
        "quality_pct": 89.0, "mt_bench_quality": 8.7, "mmlu_knowledge": 81.0,
        "score": 87.0, "pass_rate": 87.0, "speed_tps": 70.0,
        "cost_per_m_input": 5.0, "cost_per_m_output": 20.0,
        "reliability_pct": 94.0, "source_url": "frontier/anthropic-estimate",
    },
    # ── DeepSeek ──
    "deepseek-v4-pro": {
        "quality_pct": 95.0, "mt_bench_quality": 9.3, "mmlu_knowledge": 87.0,
        "score": 96.0, "pass_rate": 96.0, "speed_tps": 45.0,
        "cost_per_m_input": 4.0, "cost_per_m_output": 16.0,
        "reliability_pct": 93.0, "source_url": "frontier/deepseek-estimate",
    },
    "deepseek-v4-flash": {
        "quality_pct": 92.0, "mt_bench_quality": 9.0, "mmlu_knowledge": 84.0,
        "score": 94.0, "pass_rate": 94.0, "speed_tps": 72.0,
        "cost_per_m_input": 1.0, "cost_per_m_output": 4.0,
        "reliability_pct": 90.0, "source_url": "frontier/deepseek-estimate",
    },
    "deepseek-r1-0528": {
        "quality_pct": 90.0, "mt_bench_quality": 8.8, "mmlu_knowledge": 83.0,
        "score": 94.0, "pass_rate": 94.0, "speed_tps": 38.0,
        "cost_per_m_input": 3.0, "cost_per_m_output": 12.0,
        "reliability_pct": 92.0, "source_url": "frontier/deepseek-estimate",
    },
    # ── xAI ──
    "grok-4": {
        "quality_pct": 94.0, "mt_bench_quality": 9.2, "mmlu_knowledge": 86.0,
        "score": 93.0, "pass_rate": 93.0, "speed_tps": 50.0,
        "cost_per_m_input": 3.0, "cost_per_m_output": 15.0,
        "reliability_pct": 91.0, "source_url": "frontier/xai-estimate",
    },
    # ── Google ──
    "gemini-3-pro-preview": {
        "quality_pct": 93.0, "mt_bench_quality": 9.1, "mmlu_knowledge": 86.0,
        "score": 92.0, "pass_rate": 92.0, "speed_tps": 52.0,
        "cost_per_m_input": 5.0, "cost_per_m_output": 20.0,
        "reliability_pct": 92.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3-flash-preview": {
        "quality_pct": 88.0, "mt_bench_quality": 8.7, "mmlu_knowledge": 80.0,
        "score": 87.0, "pass_rate": 87.0, "speed_tps": 78.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 89.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3.1-flash-lite": {
        "quality_pct": 90.0, "mt_bench_quality": 8.8, "mmlu_knowledge": 82.0,
        "score": 90.0, "pass_rate": 90.0, "speed_tps": 76.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 90.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3.1-flash-live": {
        "quality_pct": 91.0, "mt_bench_quality": 8.9, "mmlu_knowledge": 83.0,
        "score": 91.0, "pass_rate": 91.0, "speed_tps": 74.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 90.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3.5-flash": {
        "quality_pct": 94.0, "mt_bench_quality": 9.2, "mmlu_knowledge": 86.0,
        "score": 94.0, "pass_rate": 94.0, "speed_tps": 70.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 93.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3.5-flash-lite": {
        "quality_pct": 92.0, "mt_bench_quality": 9.0, "mmlu_knowledge": 84.0,
        "score": 92.0, "pass_rate": 92.0, "speed_tps": 80.0,
        "cost_per_m_input": 0.25, "cost_per_m_output": 1.0,
        "reliability_pct": 91.0, "source_url": "frontier/google-estimate",
    },
    "gemini-3.6-flash": {
        "quality_pct": 95.0, "mt_bench_quality": 9.3, "mmlu_knowledge": 87.0,
        "score": 95.0, "pass_rate": 95.0, "speed_tps": 68.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 93.0, "source_url": "frontier/google-estimate",
    },
    # ── InclusionAI ──
    "ling-2.6-1t": {
        "quality_pct": 94.0, "mt_bench_quality": 9.2, "mmlu_knowledge": 85.0,
        "score": 93.0, "pass_rate": 93.0, "speed_tps": 42.0,
        "cost_per_m_input": 3.0, "cost_per_m_output": 12.0,
        "reliability_pct": 90.0, "source_url": "frontier/inclusionai-estimate",
    },
    "ling-2.6-flash": {
        "quality_pct": 87.0, "mt_bench_quality": 8.5, "mmlu_knowledge": 79.0,
        "score": 86.0, "pass_rate": 86.0, "speed_tps": 82.0,
        "cost_per_m_input": 0.8, "cost_per_m_output": 3.0,
        "reliability_pct": 88.0, "source_url": "frontier/inclusionai-estimate",
    },
    # ── Qwen ──
    "qwen3-coder-480b-a35b-instruct": {
        "quality_pct": 90.0, "mt_bench_quality": 8.8, "mmlu_knowledge": 81.0,
        "score": 96.0, "pass_rate": 96.0, "speed_tps": 30.0,
        "cost_per_m_input": 2.0, "cost_per_m_output": 8.0,
        "reliability_pct": 89.0, "source_url": "frontier/qwen-estimate",
    },
    "qwen3.7-plus": {
        "quality_pct": 91.0, "mt_bench_quality": 8.9, "mmlu_knowledge": 83.0,
        "score": 90.0, "pass_rate": 90.0, "speed_tps": 48.0,
        "cost_per_m_input": 2.0, "cost_per_m_output": 8.0,
        "reliability_pct": 90.0, "source_url": "frontier/qwen-estimate",
    },
    "qwen3.7-max": {
        "quality_pct": 93.0, "mt_bench_quality": 9.0, "mmlu_knowledge": 84.0,
        "score": 92.0, "pass_rate": 92.0, "speed_tps": 42.0,
        "cost_per_m_input": 3.0, "cost_per_m_output": 12.0,
        "reliability_pct": 91.0, "source_url": "frontier/qwen-estimate",
    },
    # ── Moonshot ──
    "kimi-k3": {
        "quality_pct": 89.0, "mt_bench_quality": 8.6, "mmlu_knowledge": 78.0,
        "score": 85.0, "pass_rate": 85.0, "speed_tps": 75.0,
        "cost_per_m_input": 0.5, "cost_per_m_output": 2.0,
        "reliability_pct": 87.0, "source_url": "frontier/moonshot-estimate",
    },
    # ── Zhipu ──
    "glm-5": {
        "quality_pct": 88.0, "mt_bench_quality": 8.5, "mmlu_knowledge": 80.0,
        "score": 87.0, "pass_rate": 87.0, "speed_tps": 65.0,
        "cost_per_m_input": 1.5, "cost_per_m_output": 6.0,
        "reliability_pct": 88.0, "source_url": "frontier/zhipu-estimate",
    },
    # ── Nex ──
    "nex-n2-pro": {
        "quality_pct": 86.0, "mt_bench_quality": 8.3, "mmlu_knowledge": 77.0,
        "score": 84.0, "pass_rate": 84.0, "speed_tps": 60.0,
        "cost_per_m_input": 2.0, "cost_per_m_output": 8.0,
        "reliability_pct": 86.0, "source_url": "frontier/nex-estimate",
    },
    "nex-n2-mini": {
        "quality_pct": 80.0, "mt_bench_quality": 7.8, "mmlu_knowledge": 72.0,
        "score": 79.0, "pass_rate": 79.0, "speed_tps": 80.0,
        "cost_per_m_input": 0.3, "cost_per_m_output": 1.0,
        "reliability_pct": 84.0, "source_url": "frontier/nex-estimate",
    },
    # ── Others ──
    "sakana-fugu-ultra": {
        "quality_pct": 85.0, "mt_bench_quality": 8.2, "mmlu_knowledge": 76.0,
        "score": 83.0, "pass_rate": 83.0, "speed_tps": 55.0,
        "cost_per_m_input": 4.0, "cost_per_m_output": 16.0,
        "reliability_pct": 85.0, "source_url": "frontier/sakana-estimate",
    },
    "cohere-north-mini-code": {
        "quality_pct": 78.0, "mt_bench_quality": 7.6, "mmlu_knowledge": 70.0,
        "score": 82.0, "pass_rate": 82.0, "speed_tps": 85.0,
        "cost_per_m_input": 1.0, "cost_per_m_output": 4.0,
        "reliability_pct": 83.0, "source_url": "frontier/cohere-estimate",
    },
    "openai-gpt-4o": {
        "quality_pct": 92.0, "mt_bench_quality": 8.9, "mmlu_knowledge": 85.0,
        "score": 90.0, "pass_rate": 90.0, "speed_tps": 58.0,
        "cost_per_m_input": 2.5, "cost_per_m_output": 10.0,
        "reliability_pct": 96.0, "source_url": "frontier/openai-estimate",
    },
    "meta-llama-4-scout": {
        "quality_pct": 86.0, "mt_bench_quality": 8.3, "mmlu_knowledge": 77.0,
        "score": 84.0, "pass_rate": 84.0, "speed_tps": 62.0,
        "cost_per_m_input": 0.6, "cost_per_m_output": 2.5,
        "reliability_pct": 87.0, "source_url": "frontier/meta-estimate",
    },
}


def _model_matches_frontier(ref: str) -> bool:
    """Heuristic: is this model likely a frontier model that needs our data?"""
    lower = ref.lower()
    frontier_indicators = [
        "gpt-5", "gpt-4o", "claude-opus-4", "deepseek-v4", "deepseek-r1-0",
        "grok-4", "gemini-3", "ling-2.6", "qwen3", "qwen3.7",
        "kimi-k3", "glm-5", "nex-n2", "sakana-fugu",
        "cohere-north", "meta-llama-4", "nvidia-nemotron-3",
    ]
    return any(indicator in lower for indicator in frontier_indicators)


def fetch(local_db_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Return frontier benchmark rows for models that either:
    (a) match known frontier indicators and don't already have real data, or
    (b) are frontier models without any benchmark rows yet.
    """
    if local_db_path is None:
        local_db_path = str(Path.home() / ".modelweaver" / "catalogue.db")
    conn = sqlite3.connect(local_db_path)
    conn.row_factory = sqlite3.Row

    # Find refs that already have real (non-synthetic) benchmark data
    real_refs = set()
    try:
        for row in conn.execute(
            "SELECT DISTINCT model_ref FROM model_benchmarks_raw WHERE is_synthetic = 0"
        ):
            real_refs.add(row["model_ref"])
    except sqlite3.OperationalError:
        real_refs = set()

    rows: List[Dict[str, Any]] = []
    for ref, scores in FRONTIER_MODELS.items():
        if ref in real_refs:
            continue
        for metric_name, raw_value in scores.items():
            if metric_name == "source_url":
                continue
            row: Dict[str, Any] = {
                "model_ref": ref,
                "benchmark_key": "frontier_estimate",
                "metric_name": metric_name,
                "raw_value": raw_value,
                "source_url": scores["source_url"],
                "is_synthetic": 1,
                "confidence": 0.6,
            }
            rows.append(row)

    # Also cover any frontier models in the catalogue that we don't have hardcoded data for
    cached_refs = set(FRONTIER_MODELS.keys())
    all_models = conn.execute(
        "SELECT ref FROM catalogue_models WHERE ref IS NOT NULL"
    ).fetchall()
    for m in all_models:
        ref = m["ref"]
        if not ref or ref in cached_refs or ref in real_refs:
            continue
        if _model_matches_frontier(ref):
            for metric_name, raw_value in _estimate_frontier_scores(ref).items():
                if metric_name == "source_url":
                    continue
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "frontier_estimate",
                    "metric_name": metric_name,
                    "raw_value": raw_value,
                    "source_url": "frontier/auto-estimate",
                    "is_synthetic": 1,
                    "confidence": 0.45,
                })

    conn.close()
    return rows


def _estimate_frontier_scores(ref: str) -> Dict[str, float]:
    """Generate reasonable estimated scores for a frontier model not in our hardcoded list."""
    import hashlib
    lower = ref.lower()
    # Jitter DÉTERMINISTE par modèle : sans lui, tous les modèles non hardcodés
    # reçoivent la MÊME valeur (82.0) → classés au même percentile ~5.6% en
    # queue de peloton (devant les hardcodés à 92+) → les modèles récents
    # fiables (gemini-3.x, etc.) sont ridiculisés. Le jitter répartit les refs
    # inconnues dans une bande [0.96, 1.04] autour du score de base.
    seed = int(hashlib.sha256(ref.encode()).hexdigest()[:8], 16)
    jit = 0.96 + (seed % 1000) / 1000.0 * 0.08
    base = {
        "quality_pct": 85.0 * jit,
        "mt_bench_quality": 8.2 * jit,
        "mmlu_knowledge": 78.0 * jit,
        "score": 82.0 * jit,
        "pass_rate": 82.0 * jit,
        "speed_tps": 60.0 * (2.0 - jit),  # plus lent si plus gros score qualité
        "cost_per_m_input": 5.0 / jit,
        "cost_per_m_output": 20.0 / jit,
        "reliability_pct": 85.0 * jit,
        "source_url": "frontier/auto-estimate",
    }
    # Adjust based on clues in the name
    if "flash" in lower or "mini" in lower or "lite" in lower:
        base["quality_pct"] *= 0.92
        base["mt_bench_quality"] *= 0.93
        base["speed_tps"] *= 1.3
        base["cost_per_m_input"] *= 0.4
        base["cost_per_m_output"] *= 0.5
    elif "pro" in lower or "plus" in lower or "max" in lower:
        base["quality_pct"] *= 1.08
        base["mt_bench_quality"] *= 1.05
        base["score"] *= 1.05
        base["pass_rate"] *= 1.05
    if "coder" in lower or ("code" in lower and "flash" not in lower):
        base["score"] *= 1.12
        base["pass_rate"] *= 1.12
    if "reasoning" in lower:
        base["score"] *= 1.15
        base["pass_rate"] *= 1.15
    # Clamp
    for key in ("quality_pct", "score", "pass_rate", "mt_bench_quality"):
        if isinstance(base.get(key), float):
            base[key] = min(99.9, base[key])
    if isinstance(base.get("mt_bench_quality"), float):
        base["mt_bench_quality"] = min(9.9, base["mt_bench_quality"])
    return base