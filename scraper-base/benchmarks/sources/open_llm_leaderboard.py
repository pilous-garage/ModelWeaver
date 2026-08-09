"""Scraper Open LLM Leaderboard v2 (Hugging Face) — benchmarks académiques.

Source : dataset `open-llm-leaderboard/contents` (parquet public, 4576 modèles).
Métriques : IFEval (instruction), BBH, MATH Lvl 5, GPQA, MUSR, MMLU-PRO,
+ Average ⬆️ (moyenne des 6). C'est la référence académique « reproductible »
(ex. fiche DeepSeek-R1, Qwen, etc.).

Mapping vers notre schéma :
  - Average / IFEval      → quality_pct (qualité globale instruction)
  - MMLU-PRO / BBH        → mmlu_knowledge (connaissances / raisonnement)
  - GPQA                  → pass_rate (question scientifique difficile)
  - MATH Lvl 5            → math (raisonnement math)
  - MUSR                  → reasoning (raisonnement multi-étapes)

Toutes les lignes sont is_synthetic=0 (scores RÉELS évalués).
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

PARQUET_URL = ("https://huggingface.co/datasets/open-llm-leaderboard/contents/"
               "resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet")


def fetch(limit: int = 5000) -> List[Dict[str, Any]]:
    """Récupère les benchmarks OLL v2 pour tous les modèles.

    Retourne des lignes normalisées (même format que AA/lmsys) avec
    is_synthetic=0, confidence=1.0.
    """
    import pandas as pd
    try:
        df = pd.read_parquet(PARQUET_URL)
    except Exception as e:
        print(f"    [open_llm_leaderboard] échec parquet: {e}")
        return []

    rows: List[Dict[str, Any]] = []
    count = 0
    for _, r in df.iterrows():
        if limit and count >= limit:
            break
        fn = str(r.get("fullname") or "")
        if not fn:
            continue
        # On garde le nom HF COMPLET (avec préfixe) : c'est la ref canonique
        # du leaderboard. `write_raw` → `model_key` fera le matching avec le
        # catalogue (meta-llama/Llama-3.1-70B-Instruct → llama-3.1-70b-instruct).
        ref = fn.strip()
        if not ref:
            continue
        src = ("https://huggingface.co/spaces/open-llm-leaderboard/"
               "open_llm_leaderboard")
        date = str(r.get("Submission Date") or "")

        # Average → qualité globale
        avg = r.get("Average \u2b06\ufe0f")
        if avg is not None and str(avg) != "nan":
            rows.append({
                "model_ref": ref,
                "benchmark_key": "open_llm_leaderboard",
                "metric_name": "quality_pct",
                "raw_value": float(avg),
                "source_url": src,
                "is_synthetic": 0,
                "confidence": 1.0,
                "meta_json": f'{{"date": "{date}"}}',
            })
        # MMLU-PRO → connaissances
        mmlu = r.get("MMLU-PRO")
        if mmlu is not None and str(mmlu) != "nan":
            rows.append({
                "model_ref": ref,
                "benchmark_key": "open_llm_leaderboard",
                "metric_name": "mmlu_knowledge",
                "raw_value": float(mmlu),
                "source_url": src,
                "is_synthetic": 0,
                "confidence": 1.0,
                "meta_json": f'{{"date": "{date}"}}',
            })
        # BBH → raisonnement
        bbh = r.get("BBH")
        if bbh is not None and str(bbh) != "nan":
            rows.append({
                "model_ref": ref,
                "benchmark_key": "open_llm_leaderboard",
                "metric_name": "reasoning",
                "raw_value": float(bbh),
                "source_url": src,
                "is_synthetic": 0,
                "confidence": 1.0,
                "meta_json": f'{{"date": "{date}"}}',
            })
        # GPQA → pass_rate (question scientifique difficile)
        gpqa = r.get("GPQA")
        if gpqa is not None and str(gpqa) != "nan":
            rows.append({
                "model_ref": ref,
                "benchmark_key": "open_llm_leaderboard",
                "metric_name": "pass_rate",
                "raw_value": float(gpqa),
                "source_url": src,
                "is_synthetic": 0,
                "confidence": 1.0,
                "meta_json": f'{{"date": "{date}"}}',
            })
        # MATH Lvl 5 → math
        math5 = r.get("MATH Lvl 5")
        if math5 is not None and str(math5) != "nan":
            rows.append({
                "model_ref": ref,
                "benchmark_key": "open_llm_leaderboard",
                "metric_name": "math",
                "raw_value": float(math5),
                "source_url": src,
                "is_synthetic": 0,
                "confidence": 1.0,
                "meta_json": f'{{"date": "{date}"}}',
            })
        count += 1
    return rows


if __name__ == "__main__":
    result = fetch()
    print(f"open_llm_leaderboard: {len(result)} lignes réelles")
    models = {r["model_ref"] for r in result}
    print(f"modèles distincts: {len(models)}")
