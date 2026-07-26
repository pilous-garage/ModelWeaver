"""Sources de benchmarks — version exhaustive.

Chaque source exporte :
    fetch() -> List[Dict]

où chaque dict a les clés standardisées :
    model_ref      : str  ('openai/gpt-4o')
    benchmark_key  : str  (nom de la source)
    metric_name    : str  (nom de la métrique)
    raw_value      : float
    source_url     : str
    is_synthetic   : int  (0 ou 1, défaut 0)
    confidence     : float (0.0–1.0, défaut 1.0)
"""

from . import frontier
from . import lmsys_arena
from . import arena_hard
from . import synthetic


SOURCES = {
    "lmsys_arena": lmsys_arena,
    "arena_hard": arena_hard,
    "frontier_estimate": frontier,
    "synthetic_catalogue": synthetic,
}


def fetch_all(local_db_path: str) -> dict:
    """Exécute toutes les sources et retourne {source_name: [rows]}."""
    results = {}
    for name, mod in SOURCES.items():
        try:
            if name == "synthetic_catalogue":
                rows = mod.fetch_all_models(local_db_path)
            else:
                rows = mod.fetch()
            results[name] = rows
            print(f"  ✓ {name}: {len(rows)} lignes")
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            results[name] = []
    return results


SOURCE_NAMES = list(SOURCES.keys())