"""Sources de benchmarks.

Chaque source exporte une fonction :
    fetch() -> List[Dict]

où chaque dict a les clés :
    model_ref     : str   ('openai/gpt-4o')
    benchmark_key : str   ('lmsys_arena_elo', 'artificial_analysis', 'swe_bench_verified')
    metric_name   : str   ('elo', 'quality', 'speed_tps', 'cost_per_m_input', 'pass_rate')
    raw_value     : float
    source_url    : str
"""

from . import seeded


SOURCES = {
    "seeded": seeded,
}


def fetch_all() -> dict:
    """Exécute toutes les sources et retourne {source_name: [rows, ...]}."""
    results = {}
    for name, mod in SOURCES.items():
        try:
            rows = mod.fetch()
            results[name] = rows
            print(f"  ✓ {name}: {len(rows)} models")
        except Exception as e:
            print(f"  ✗ {name}: {e}")
            results[name] = []
    return results
