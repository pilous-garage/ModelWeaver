"""Test : synchronisation des tarifs dans le catalogue (source GitHub)."""

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.catalogue.pricing import _normalize, _to_str, merge_pricing
from modules.sql.db import CatalogueDB


class TestPricingNormalize(unittest.TestCase):
    def test_to_str(self):
        self.assertIsNone(_to_str(None))
        self.assertEqual(_to_str(5e-08), "5e-08")
        self.assertEqual(_to_str(0), "0.0")

    def test_normalize_free_tier(self):
        n = _normalize({"input_cost_per_token": 0, "output_cost_per_token": 0,
                        "max_input_tokens": 128000, "max_output_tokens": 4096})
        self.assertEqual(n["cost_per_input_token"], "0.0")
        self.assertEqual(n["context_window_tokens"], 128000)
        self.assertEqual(n["max_output_tokens"], 4096)

    def test_normalize_paid(self):
        n = _normalize({"input_cost_per_token": 2.5e-6,
                        "output_cost_per_token": 1e-5, "max_input_tokens": 200000})
        self.assertEqual(n["cost_per_input_token"], "2.5e-06")
        self.assertEqual(n["cost_per_output_token"], "1e-05")
        self.assertEqual(n["context_window_tokens"], 200000)


class TestMergePricing(unittest.TestCase):
    def setUp(self):
        self.cat = CatalogueDB()
        self.cat.conn.execute("UPDATE provider_models SET "
                              "cost_per_input_token=NULL, cost_per_output_token=NULL, "
                              "context_window_tokens=NULL, max_output_tokens=NULL")
        self.cat.conn.commit()

    def tearDown(self):
        self.cat.close()

    def test_merge_fills_cost_and_context(self):
        pricing = {
            "groq/llama-3.1-8b-instant": {
                "input_cost_per_token": 0, "output_cost_per_token": 0,
                "max_input_tokens": 131072, "max_output_tokens": 8192,
            },
            "openai/gpt-4o-mini": {
                "input_cost_per_token": 1.5e-7, "output_cost_per_token": 6e-7,
                "max_input_tokens": 128000,
            },
        }
        stats = merge_pricing(self.cat, pricing, dry_run=False)
        self.assertEqual(stats["matched"], 2)
        self.assertEqual(stats["free_tier"], 1)
        # groq free
        r = self.cat.conn.execute(
            "SELECT cost_per_input_token, context_window_tokens FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id "
            "WHERE p.ref='groq' AND pm.provider_model_name='llama-3.1-8b-instant'").fetchone()
        self.assertIsNotNone(r)
        self.assertEqual(r["cost_per_input_token"], "0.0")
        self.assertEqual(r["context_window_tokens"], 131072)
        # openai paid
        r2 = self.cat.conn.execute(
            "SELECT cost_per_input_token, cost_per_output_token FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id "
            "WHERE p.ref='openai' AND pm.provider_model_name='gpt-4o-mini'").fetchone()
        self.assertEqual(r2["cost_per_input_token"], "1.5e-07")
        self.assertEqual(r2["cost_per_output_token"], "6e-07")

    def test_only_free_tier_filters(self):
        pricing = {
            "groq/llama-3.1-8b-instant": {"input_cost_per_token": 0, "output_cost_per_token": 0},
            "openai/gpt-4o-mini": {"input_cost_per_token": 1.5e-7, "output_cost_per_token": 6e-7},
        }
        stats = merge_pricing(self.cat, pricing, dry_run=False, only_free_tier=True)
        self.assertEqual(stats["matched"], 1)  # openai exclu
        self.assertEqual(stats["free_tier"], 1)


class TestCatalogueAliases(unittest.TestCase):
    def setUp(self):
        self.cat = CatalogueDB()

    def tearDown(self):
        self.cat.close()

    def test_add_and_resolve_alias(self):
        self.cat.add_alias("litellm_github", "provider", "nvidia_nim", "nvidia")
        self.assertEqual(
            self.cat.resolve_alias("litellm_github", "provider", "nvidia_nim"), "nvidia")
        # priorite : on met a jour avec une autre ref + prio plus haute
        self.cat.add_alias("litellm_github", "provider", "nvidia_nim", "nv", priority=5)
        self.assertEqual(
            self.cat.resolve_alias("litellm_github", "provider", "nvidia_nim"), "nv")
        amap = self.cat.alias_map("litellm_github", "provider")
        self.assertEqual(amap["nvidia_nim"], "nv")

    def test_merge_uses_alias_table(self):
        # alias gemini -> google, puis on merge un tarif sous cle 'gemini/...'
        # qui doit tomber sur le provider 'google' du catalogue.
        self.cat.add_alias("litellm_github", "provider", "gemini", "google")
        # on cible un provider_model_name de google existant
        row = self.cat.conn.execute(
            "SELECT pm.id, pm.provider_model_name FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id WHERE p.ref='google' "
            "AND pm.context_window_tokens IS NULL LIMIT 1").fetchone()
        self.assertIsNotNone(row)
        pricing = {f"gemini/{row['provider_model_name']}": {
            "input_cost_per_token": 0, "output_cost_per_token": 0,
            "max_input_tokens": 99999}}
        stats = merge_pricing(self.cat, pricing, dry_run=False, source="litellm_github")
        self.assertEqual(stats["matched"], 1)
        r = self.cat.conn.execute(
            "SELECT context_window_tokens FROM provider_models WHERE id=?",
            (row["id"],)).fetchone()
        self.assertEqual(r["context_window_tokens"], 99999)

    def test_seed_litellm_aliases_idempotent(self):
        from modules.catalogue.pricing import seed_litellm_aliases, LITELLM_ALIAS_SOURCE
        self.cat.conn.execute("DELETE FROM catalogue_aliases WHERE source=?", (LITELLM_ALIAS_SOURCE,))
        self.cat.conn.commit()
        n1 = seed_litellm_aliases(self.cat)
        n2 = seed_litellm_aliases(self.cat)
        self.assertGreater(n1, 0)
        self.assertEqual(n2, 0)  # deja peuplé
        total = self.cat.conn.execute(
            "SELECT COUNT(*) FROM catalogue_aliases WHERE source=?",
            (LITELLM_ALIAS_SOURCE,)).fetchone()[0]
        self.assertEqual(total, n1)


if __name__ == "__main__":
    unittest.main()
