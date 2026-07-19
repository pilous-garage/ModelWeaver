"""Test : decouverte automatique d'alias depuis le cache litellm."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.catalogue.alias_discovery import (
    discover_litellm_aliases, _match_model, _provider_heuristic, _norm_name)
from modules.catalogue.pricing import fetch_litellm_pricing
from modules.sql.db import CatalogueDB


class TestDiscoveryHelpers(unittest.TestCase):
    def test_norm_name(self):
        self.assertEqual(_norm_name("Gemini-2.5/Flash"), "gemini_2_5_flash")
        self.assertEqual(_norm_name("openai/gpt-4o"), "openai_gpt_4o")

    def test_provider_heuristic(self):
        provs = ["openai", "google", "nvidia", "groq", "azure"]
        # egalite exacte
        self.assertEqual(_provider_heuristic("openai", provs), "openai")
        # suffixe : azure_ai -> azure
        self.assertEqual(_provider_heuristic("azure_ai", provs), "azure")
        # sans correspondance -> None (gemini->google necessite un alias declare)
        self.assertIsNone(_provider_heuristic("gemini", provs))
        self.assertIsNone(_provider_heuristic("totally_unknown_xyz", provs))

    def test_match_model_exact(self):
        models = [{"model_ref": "gpt-4o-mini", "pmn": "gpt-4o-mini"}]
        self.assertEqual(_match_model("gpt-4o-mini", models), "gpt-4o-mini")

    def test_match_model_suffix(self):
        models = [{"model_ref": "google/gemini-2.5-flash", "pmn": "gemini-2.5-flash"}]
        self.assertEqual(_match_model("gemini/gemini-2.5-flash", models),
                         "google/gemini-2.5-flash")

    def test_match_model_ambiguous(self):
        models = [
            {"model_ref": "a/llama-3", "pmn": "llama-3"},
            {"model_ref": "b/llama-3", "pmn": "llama-3"},
        ]
        # plusieurs candidats -> None (ambiguite)
        self.assertIsNone(_match_model("llama-3", models))


class TestDiscoverLitellm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = CatalogueDB()
        cls.lit = fetch_litellm_pricing()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def setUp(self):
        # nettoyage des aliases decouverts pour ce target/source
        self.cat.conn.execute(
            "DELETE FROM catalogue_aliases WHERE target='litellm'")
        self.cat.conn.commit()

    def test_discover_inserts_aliases(self):
        # seed des alias provider connus (gemini->google, nvidia_nim->nvidia...)
        from modules.catalogue.pricing import seed_litellm_aliases
        seed_litellm_aliases(self.cat)
        stats = discover_litellm_aliases(self.cat, self.lit, dry_run=False)
        self.assertGreater(stats["provider_aliases"], 0)
        self.assertGreater(stats["model_aliases"], 0)
        # google doit etre resolu (gemini -> google, via seed)
        self.assertEqual(
            self.cat.resolve_alias("litellm", "provider", "gemini"), "google")
        # un alias model gemini doit exister
        models = self.cat.alias_map("litellm", "model")
        self.assertTrue(any(k.startswith("gemini/") for k in models))

    def test_discover_idempotent(self):
        n1 = discover_litellm_aliases(self.cat, self.lit, dry_run=False)
        total1 = self.cat.conn.execute(
            "SELECT COUNT(*) FROM catalogue_aliases WHERE target='litellm'").fetchone()[0]
        n2 = discover_litellm_aliases(self.cat, self.lit, dry_run=False)
        total2 = self.cat.conn.execute(
            "SELECT COUNT(*) FROM catalogue_aliases WHERE target='litellm'").fetchone()[0]
        self.assertEqual(total1, total2)  # pas de doublon (UNIQUE)


if __name__ == "__main__":
    unittest.main()
