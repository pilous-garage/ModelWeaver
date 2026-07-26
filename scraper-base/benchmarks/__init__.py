"""Benchmark Scrapers — collecte de scores de modèles LLM.

Usage (admin machine) :
    python -m scraper-base.benchmarks.run_all

Ce module scrape les benchmarks publics, normalise les scores
en percentiles, et les écrit dans la base Turso distante.
"""
