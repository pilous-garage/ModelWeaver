"""Scraper LMSYS Chatbot Arena ELO — depuis elo_results_*.pkl (Hugging Face Space).

Le CSV `leaderboard_table_*.csv` n'a PAS de colonne ELO (seulement MT-bench + MMLU).
Les vrais ratings ELO vivent dans les fichiers `elo_results_*.pkl` du Space
`lmarena-ai/arena-leaderboard`, sous la forme de dataframes pandas par
catégorie : text / vision / image / image-edit / webdev.

Le .pkl contient des figures plotly (obsolètes dans plotly>=6). On les déballonne
avec un Unpickler personnalisé qui remplace les classes plotly par des stubs :
AUCUNE dépendance plotly requise, seule pandas doit être installée.

Chaque modèle text reçoit une ligne `lmsys_arena_elo` / `elo` (rating réel).
Les catégories vision/image/webdev sont ignorées (hors scope LLM texte).
"""

from __future__ import annotations

import io
import pickle
import re
import urllib.request
import ssl
from typing import Any, Dict, List, Optional


HF_SPACE_HOST = "https://lmarena-ai-arena-leaderboard.static.hf.space"
PKL_TEMPLATE = f"{HF_SPACE_HOST}/elo_results_{{date}}.pkl"


class _StubPlotly:
    """Remplace une classe plotly lors du unpickle (ignorée en bloc)."""

    def __init__(self, *args, **kwargs):
        self.data = []
        self.layout = {}

    def __getattr__(self, name):
        return lambda *a, **k: None

    def __iter__(self):
        return iter(())

    def __getitem__(self, key):
        return None


class _StubUnpickler(pickle.Unpickler):
    """Unpickler qui neutralise les objets plotly (heatmapgl etc. plotly>=6)."""

    def find_class(self, module, name):
        if "plotly" in module:
            return _StubPlotly
        return super().find_class(module, name)


def _latest_pkl_date() -> str:
    """La date du .pkl ELO le plus récent dans le Space HF."""
    url = "https://huggingface.co/api/spaces/lmarena-ai/arena-leaderboard"
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        import json
        with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
            data = json.loads(resp.read().decode())
        pkls = [s["rfilename"] for s in data.get("siblings", [])
                if s["rfilename"].startswith("elo_results_")]
        if pkls:
            pkls.sort(reverse=True)
            return pkls[0].replace("elo_results_", "").replace(".pkl", "")
    except Exception:
        pass
    return "20250829"


def _normalize_model_name(name: str) -> str:
    """Normalise le nom du leaderboard ELO (ex. gemini-2.5-pro, gpt-5-high)."""
    n = (name or "").strip().lower()
    # chatgpt-4o-latest-20250326 → chatgpt-4o-latest
    n = re.sub(r"-\d{6,8}$", "", n)
    n = n.strip("-")
    return n


def fetch(limit: int = 500) -> List[Dict[str, Any]]:
    """Récupère les ELO text du leaderboard LMSYS.

    Retourne une ligne par modèle : benchmark_key='lmsys_arena_elo',
    metric_name='elo', raw_value=rating. is_synthetic=0 (données réelles).
    """
    date = _latest_pkl_date()
    url = PKL_TEMPLATE.format(date=date)
    ctx = ssl.create_default_context()
    req = urllib.request.Request(url, headers={"User-Agent": "ModelWeaver/0.8.5"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as resp:
            raw = resp.read()
    except Exception as e:
        print(f"  ⚠ LMSYS ELO pkl ({date}) non dispo ({e})")
        return []

    try:
        obj = _StubUnpickler(io.BytesIO(raw)).load()
    except Exception as e:
        print(f"  ⚠ LMSYS ELO unpickle ({date}) échoué: {e}")
        return []

    df = None
    try:
        text = obj.get("text", {})
        df = text.get("full", {}).get("leaderboard_table_df")
    except Exception:
        df = None
    if df is None:
        print(f"  ⚠ LMSYS ELO : dataframe text/full introuvable dans le pkl")
        return []

    rows: List[Dict[str, Any]] = []
    seen = set()
    try:
        index = list(df.index)
        ratings = list(df["rating"])
    except Exception as e:
        print(f"  ⚠ LMSYS ELO : dataframe illisible: {e}")
        return []

    for name, rating in zip(index, ratings):
        if limit and len(rows) >= limit:
            break
        ref = _normalize_model_name(str(name))
        if not ref or ref in seen:
            continue
        seen.add(ref)
        try:
            rv = float(rating)
        except (TypeError, ValueError):
            continue
        rows.append({
            "model_ref": ref,
            "benchmark_key": "lmsys_arena_elo",
            "metric_name": "elo",
            "raw_value": rv,
            "source_url": url,
            "is_synthetic": 0,
            "confidence": 1.0,
        })
    return rows


if __name__ == "__main__":
    result = fetch()
    print(f"lmsys_arena_elo: {len(result)} lignes réelles")
    for r in result[:10]:
        print(f"  {r['model_ref']:38s} elo={r['raw_value']:.1f}")
