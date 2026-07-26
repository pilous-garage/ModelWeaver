"""Scraper LMSYS Arena-Hard Auto Leaderboard from HF Space CSV.

Source: lmarena-ai/arena-leaderboard → arena_hard_auto_leaderboard_v0.1.csv

Metric: Elo-style score (higher = better).
"""

from __future__ import annotations

import csv
import io
import urllib.request
import urllib.error
import ssl
from typing import Any, Dict, List


HF_SPACE_HOST = "https://lmarena-ai-arena-leaderboard.static.hf.space"
URL = f"{HF_SPACE_HOST}/arena_hard_auto_leaderboard_v0.1.csv"


def _normalize_model_name(name: str) -> str:
    """Strip version/date suffixes from LMSYS model identifiers."""
    import re
    n = name.strip().lower()
    n = re.sub(r"\-?\d{4}.*$", "", n)
    n = re.sub(r"\-v\d+$", "", n)
    n = re.sub(r"\-(latest|turbo|snapshot|preview|exp|beta)$", "", n)
    n = n.strip("-").strip()
    return n


def fetch() -> List[Dict[str, Any]]:
    ctx = ssl.create_default_context()
    req = urllib.request.Request(URL, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
            csv_bytes = resp.read()
    except Exception as e:
        print(f"  ⚠ Arena-Hard non dispo ({e})")
        return []
    reader = csv.DictReader(io.TextIOWrapper(io.BytesIO(csv_bytes), encoding="utf-8"))
    rows = []
    seen = set()
    for row in reader:
        model = row.get("model", "")
        if not model:
            continue
        ref = _normalize_model_name(model)
        try:
            score = float(row.get("score", 0) or 0)
        except ValueError:
            score = 0.0
        if score == 0.0 or ref in seen:
            continue
        seen.add(ref)
        rows.append({
            "model_ref": ref,
            "benchmark_key": "arena_hard_auto",
            "metric_name": "score",
            "raw_value": score,
            "source_url": URL,
        })
    return rows