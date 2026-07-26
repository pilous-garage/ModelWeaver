"""Scraper LMSYS Chatbot Arena Leaderboard from Hugging Face Space CSV.

The leaderboard data is published as daily CSV snapshots in the HF Space:
  lmarena-ai/arena-leaderboard

Each CSV file: leaderboard_table_YYYYMMDD.csv

Metrics:
  - MT-bench (score): quality score 0-10 (style/chat preferences)
  - MMLU: proportion 0.0-1.0 (knowledge benchmark)

We derive an ELO-style score from the ranking position.
"""

from __future__ import annotations

import csv
import io
import json
import re
import urllib.request
import urllib.error
import ssl
from typing import Any, Dict, List, Optional


HF_SPACE_HOST = "https://lmarena-ai-arena-leaderboard.static.hf.space"
LEADERBOARD_CSV_TEMPLATE = (
    "{host}/leaderboard_table_{date}.csv"
)


def _latest_csv_date() -> str:
    """Tentative: derive latest CSV date from the Space API."""
    url = "https://huggingface.co/api/spaces/lmarena-ai/arena-leaderboard"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        siblings = data.get("siblings", [])
        csvs = [s["rfilename"] for s in siblings if "leaderboard_table_20" in s["rfilename"]]
        if csvs:
            csvs.sort(reverse=True)
            stem = csvs[0]
            return stem.replace("leaderboard_table_", "").replace(".csv", "")
    except Exception:
        pass
    return "20250804"


def _normalize_model_name(name: str) -> str:
    n = name.strip().lower()
    n = re.sub(r"\-?\d{4}.*$", "", n)       # strip version dates
    n = re.sub(r"\-v\d+$", "", n)
    n = re.sub(r"\-(latest|turbo|snapshot|preview|exp|beta)$", "", n)
    n = n.strip("-").strip()
    return n


def fetch(limit: int = 500) -> List[Dict[str, Any]]:
    rows = []
    date = _latest_csv_date()
    url = LEADERBOARD_CSV_TEMPLATE.format(host=HF_SPACE_HOST, date=date)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            csv_bytes = resp.read()
    except Exception as e:
        print(f"  ⚠ LMSYS leaderboard CSV ({date}) non dispo ({e})")
        return []
    reader = csv.DictReader(io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="utf-8"))
    rank = 0
    for row in reader:
        if rank >= limit:
            break
        rank += 1
        model_name = row.get("Model", "")
        if not model_name:
            continue
        mt_bench = row.get("MT-bench (score)", "")
        mmlu = row.get("MMLU", "")
        ref = _normalize_model_name(model_name)
        if not ref:
            continue
        try:
            raw = float(mt_bench) if mt_bench else 0.0
        except ValueError:
            raw = 0.0
        if raw == 0.0 and not mmlu:
            continue
        rows.append({
            "model_ref": ref,
            "benchmark_key": "lmsys_arena",
            "metric_name": "mt_bench_quality",
            "raw_value": raw,
            "source_url": url,
            "lmsys_mmlu": float(mmlu) if mmlu and mmlu != "-" else None,
            "lmsys_rank": rank,
        })
        if mmlu and mmlu != "-":
            try:
                mmlu_val = float(mmlu)
                rows.append({
                    "model_ref": ref,
                    "benchmark_key": "lmsys_arena",
                    "metric_name": "mmlu_knowledge",
                    "raw_value": mmlu_val * 100,  # scale to 0-100
                    "source_url": url,
                    "lmsys_rank": rank,
                })
            except ValueError:
                pass
    return rows