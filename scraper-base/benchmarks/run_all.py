#!/usr/bin/env python3
"""Orchestrateur des scrapers de benchmarks LLM.

Usage :
    python -m scraper-base.benchmarks.run_all          # → Turso distant
    python -m scraper-base.benchmarks.run_all --local  # → catalogue.db local

Ce script :
    1. Scrape toutes les sources de benchmarks
    2. Normalise les scores en percentiles
    3. Consolide dans model_efficacy
    4. Écrit sur la base Turso distante (ou locale avec --local)

À mettre en cron hebdomadaire sur la machine admin :
    0 6 * * 1 cd /opt/modelweaver && python -m scraper-base.benchmarks.run_all
"""

import sys
import os
import runpy


def main():
    local = "--local" in sys.argv

    print("=" * 60)
    print("ModelWeaver — LLM Benchmark Scraper")
    print(f"Target: {'local catalogue' if local else 'Turso remote'}")
    print("=" * 60)

    # Import dynamique : on ajoute scraper-base/ au path
    # pour pouvoir faire "from benchmarks.consolidate import run"
    _self_dir = os.path.dirname(os.path.abspath(__file__))  # .../scraper-base/benchmarks/
    _scraper_base = os.path.dirname(_self_dir)               # .../scraper-base/
    if _scraper_base not in sys.path:
        sys.path.insert(0, _scraper_base)

    from benchmarks.consolidate import run
    run(local_mode=local)


if __name__ == "__main__":
    main()
