"""Synthetic benchmark data generator for ALL catalogue models.

Generates plausible (but marked as synthetic) benchmark rows for every
model in the local catalogue that has NO real data from LMSYS, Arena-Hard
or other live sources.

Method:
  - Quality score: derived from age (newer = better), size (bigger = better,
    diminishing returns), and architecture knowledge.
  - Speed score: derived inversely from parameter count (smaller = faster).
  - Cost score: derived from parameter count and openweights status.
  - Reliability: derived from release_year stability (older = proven).
  - Reliability bonus for models with known high reliability (MMLU, etc.).

Every synthetic row carries ``is_synthetic = 1`` and ``confidence = 0.3``
so downstream consumers can discount them relative to real data (confidence 1.0).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any, Dict, List, Optional

import sqlite3


QUALITY_WEIGHTS = {
    "Transformer": 50,
    "MixtureOfExperts": 55,
    "Mamba": 45,
    "RWKV": 40,
    "RetNet": 42,
    "Gemma": 48,
}

SIZE_BONUS_TABLE = [
    (1_000_000_000_000, 35),
    (400_000_000_000, 33),
    (100_000_000_000, 30),
    (70_000_000_000, 27),
    (34_000_000_000, 24),
    (14_000_000_000, 21),
    (7_000_000_000, 18),
    (3_000_000_000, 15),
    (1_000_000_000, 12),
    (0, 8),
]

COST_BASE = {
    "gpt-": 100,
    "o1": 120,
    "o3-": 50,
    "claude": 35,
    "gemini": 8,
    "gemma": 5,
    "meta/llama": 6,
    "mistral/": 10,
    "deepseek/": 7,
    "qwen/": 4,
    "cohere/command": 12,
    "databricks/": 15,
}
DEFAULT_COST = 20


def _parse_param_count(text: Optional[str]) -> int:
    if not text:
        return 0
    text = text.replace(",", "").strip().lower()
    if text.endswith("b"):
        try:
            return int(float(text[:-1]) * 1_000_000_000)
        except ValueError:
            return 0
    if text.endswith("t"):
        try:
            return int(float(text[:-1]) * 1_000_000_000_000)
        except ValueError:
            return 0
    try:
        return int(float(text))
    except ValueError:
        return 0


def _detect_provider(ref: str) -> str:
    if "/" in ref:
        return ref.split("/", 1)[0]
    return ref.split("-")[0] if "-" in ref else ref.split(".")[0]


def _quality_bonus(ref: str, release_year: Optional[int], architecture: Optional[str],
                   modality: Optional[str], target_use: Optional[str]) -> float:
    bonus = 0.0
    arch = (architecture or "").lower()
    if "mixture" in arch or "moe" in arch:
        bonus += 3
    if "reasoning" in (target_use or "").lower():
        bonus += 2
    if "vision" in (modality or "").lower():
        bonus += 1.5
    if "multilingual" in (target_use or "").lower():
        bonus += 1
    if release_year and release_year >= 2024:
        bonus += 4
    elif release_year and release_year >= 2023:
        bonus += 2
    return bonus


def _speed_score(param_count: int) -> float:
    for threshold, score in SIZE_BONUS_TABLE:
        if param_count >= threshold:
            return score
    return 5


def _cost_score(ref: str, param_count: int, open_weights: bool) -> float:
    provider = _detect_provider(ref)
    base = COST_BASE.get(provider, DEFAULT_COST)
    size_factor = min(100, max(1, 100 - param_count / 1_000_000_000))
    if open_weights:
        size_factor *= 0.7
    return round(base * (100 - size_factor * 0.5) / 100, 2)


def _synth_value(seed: int, lo: float, hi: float) -> float:
    """Deterministic pseudo-random in [lo, hi] from model ref hash."""
    rng = int(hashlib.sha256(str(seed).encode()).hexdigest()[:8], 16)
    return round(lo + (rng % 1000) / 1000 * (hi - lo), 2)


def _generate_model_rows(
    model_id: int,
    ref: str,
    release_year: Optional[int],
    architecture: Optional[str],
    modality: Optional[str],
    target_use: Optional[str],
    is_open_weights: bool,
) -> List[Dict[str, Any]]:
    rows = []
    param_count = 0

    quality_bonus = _quality_bonus(ref, release_year, architecture, modality, target_use)

    for metric, (lo, hi) in {
        "quality_pct": (55 + quality_bonus, 98 + quality_bonus),
        "speed_tps": (_speed_score(param_count) - 3, _speed_score(param_count) + 3),
        "cost_per_m_input": (_cost_score(ref, param_count, is_open_weights) * 0.7,
                            _cost_score(ref, param_count, is_open_weights) * 1.3),
        "cost_per_m_output": (_cost_score(ref, param_count, is_open_weights) * 0.8,
                             _cost_score(ref, param_count, is_open_weights) * 1.2),
        "reliability_pct": (65, 90 if release_year and release_year < 2024 else 85),
    }.items():
        rows.append({
            "model_ref": ref,
            "benchmark_key": "synthetic_catalogue",
            "metric_name": metric,
            "raw_value": _synth_value(model_id * 1000 + hash(metric) % 10000, lo, hi),
            "source_url": "synthetic/catalogue-estimate",
            "is_synthetic": 1,
            "confidence": 0.3,
        })

    return rows


def fetch_all_models(local_db_path: str) -> List[Dict[str, Any]]:
    """Fetch rows for ALL models in catalogue that lack real benchmark data."""
    conn = sqlite3.connect(local_db_path)
    conn.row_factory = sqlite3.Row

    existing_refs = set()
    try:
        for row in conn.execute(
            "SELECT DISTINCT model_ref FROM model_benchmarks_raw WHERE is_synthetic = 0"
        ):
            existing_refs.add(row["model_ref"])
    except sqlite3.OperationalError:
        # Column or table may not exist yet
        existing_refs = set()

    all_models = conn.execute(
        "SELECT id, ref, release_year, architecture, modality, "
        "target_use, is_open_weights FROM catalogue_models"
    ).fetchall()

    rows = []
    for m in all_models:
        ref = m["ref"]
        if not ref or ref in existing_refs:
            continue
        rows.extend(_generate_model_rows(
            m["id"], ref,
            m["release_year"], m["architecture"],
            m["modality"], m["target_use"],
            bool(m["is_open_weights"]),
        ))

    conn.close()
    return rows