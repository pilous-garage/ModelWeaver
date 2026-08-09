#!/usr/bin/env python3
"""Enrichit benchmarks_1111_modeles_v*.csv depuis l'Open LLM Leaderboard v2.

Le dataset open-llm-leaderboard/contents (4576 modèles) contient les 6
benchmarks académiques (IFEval, BBH, MATH Lvl 5, GPQA, MUSR, MMLU-PRO) pour
des milliers de modèles. On matche nos 1110 modèles « sans benchmark réel »
par model_key normalisé, puis on remplit les colonnes.

Usage :
    python3 scripts/enrich_benchmarks_oll.py [--csv benchmarks_1111_modeles_v1.csv]

Le fichier parquet est téléchargé en cache (/tmp/opencode/oll.parquet) et
réutilisé. Seules les lignes vides (status='not yet verified') sont remplies ;
les lignes déjà vérifiées ne sont pas écrasées.
"""

from __future__ import annotations

import argparse
import csv
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scraper-base"))

from benchmarks.model_key import model_key  # noqa: E402

PARQUET_URL = ("https://huggingface.co/datasets/open-llm-leaderboard/contents/"
               "resolve/refs%2Fconvert%2Fparquet/default/train/0000.parquet")
PARQUET_CACHE = Path("/tmp/opencode/oll.parquet")

# Colonnes CSV cibles ← colonnes du dataset.
FIELDS = {
    "IFEval": "IFEval",
    "BBH": "BBH",
    "MATH_Lvl5": "MATH Lvl 5",
    "GPQA": "GPQA",
    "MUSR": "MUSR",
    "MMLU_PRO": "MMLU-PRO",
    "Average": "Average \u2b06\ufe0f",
}


def download_parquet() -> Path:
    if PARQUET_CACHE.exists() and PARQUET_CACHE.stat().st_size > 100_000:
        return PARQUET_CACHE
    PARQUET_CACHE.parent.mkdir(parents=True, exist_ok=True)
    import urllib.request
    print(f"Téléchargement du parquet ({PARQUET_URL})…")
    urllib.request.urlretrieve(PARQUET_URL, PARQUET_CACHE)
    print(f"  → {PARQUET_CACHE} ({PARQUET_CACHE.stat().st_size // 1024} KB)")
    return PARQUET_CACHE


def load_leaderboard():
    """Retourne {model_key_normalisé: row} pour le meilleur run de chaque modèle."""
    import pandas as pd
    path = download_parquet()
    df = pd.read_parquet(path)
    best: dict = {}
    for _, r in df.iterrows():
        fn = str(r.get("fullname") or "")
        if not fn:
            continue
        key = model_key(fn)
        if not key or key in best:
            continue  # premier run rencontré (le plus récent en général)
        best[key] = r
    return best


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default="benchmarks_1111_modeles_v1.csv")
    args = ap.parse_args()
    csv_path = Path(args.csv)
    if not csv_path.exists():
        print(f"CSV introuvable: {csv_path}")
        sys.exit(1)

    print("Chargement du leaderboard Open LLM v2…")
    lb = load_leaderboard()
    print(f"  {len(lb)} modèles uniques dans le leaderboard")

    with open(csv_path, newline="", encoding="utf-8-sig") as fh:
        reader = csv.DictReader(fh)
        rows = list(reader)
        fieldnames = reader.fieldnames

    filled = skipped = notfound = 0
    for r in rows:
        model = (r.get("model") or "").strip()
        if not model:
            skipped += 1
            continue
        if (r.get("status") or "").strip() == "verified benchmark found":
            skipped += 1
            continue
        key = model_key(model)
        lb_row = lb.get(key)
        if lb_row is None:
            notfound += 1
            continue
        # Remplir les colonnes
        for csv_col, lb_col in FIELDS.items():
            val = lb_row.get(lb_col)
            if val is not None and not str(r.get(csv_col) or "").strip():
                r[csv_col] = f"{float(val):.6f}"
        r["source_model"] = lb_row.get("fullname") or ""
        r["source"] = "Open LLM Leaderboard (Hugging Face)"
        r["source_url"] = "https://huggingface.co/spaces/open-llm-leaderboard/open_llm_leaderboard"
        r["source_date"] = str(lb_row.get("Submission Date") or "")
        r["confidence"] = "high"
        r["status"] = "verified benchmark found"
        filled += 1

    out_path = csv_path
    with open(out_path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    total = sum(1 for r in rows if r.get("model"))
    verified = sum(1 for r in rows
                   if (r.get("status") or "").strip() == "verified benchmark found")
    print(f"\nRemplis automatiquement: {filled}")
    print(f"Déjà vérifiés (non touchés): {skipped}")
    print(f"Non trouvés dans le leaderboard: {notfound}")
    print(f"Total: {verified}/{total} modèles vérifiés ({100 * verified / total:.1f}%)")
    print(f"Écrit dans {out_path}")


if __name__ == "__main__":
    main()
