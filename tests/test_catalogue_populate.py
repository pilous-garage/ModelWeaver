"""Test : peuplement des provider_models depuis litellm + API NVIDIA."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.catalogue.populate import (
    populate_provider_models, populate_nvidia_from_api, _ensure_provider)
from modules.catalogue.pricing import fetch_litellm_pricing
from modules.sql.db import CatalogueDB


class TestPopulateLitellm(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = CatalogueDB()
        cls.lit = fetch_litellm_pricing()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def setUp(self):
        # on isole : supprime les provider_models/aliases de test
        self.cat.conn.execute(
            "DELETE FROM provider_models WHERE provider_model_name LIKE 'unittest/%'")
        self.cat.conn.execute(
            "DELETE FROM catalogue_models WHERE ref IN ('unittest-gpt-x','unittest-llama-x')")
        self.cat.conn.execute(
            "DELETE FROM catalogue_providers WHERE ref='unittestprov'")
        self.cat.conn.commit()

    def test_ensure_provider_creates(self):
        pid = _ensure_provider(self.cat, "unittestprov")
        self.assertGreater(pid, 0)
        pid2 = _ensure_provider(self.cat, "unittestprov")
        self.assertEqual(pid, pid2)  # idempotent

    def test_populate_creates_links_and_models(self):
        # entree litellm artefact pour un provider EXISTANT (groq) et modele
        # inexistant -> doit creer catalogue_models + provider_models.
        name = "groq/populatetest-gpt-x"
        mref = "populatetest-gpt-x"
        # nettoyage prealable
        self.cat.conn.execute(
            "DELETE FROM provider_models WHERE provider_model_name=?", (name,))
        self.cat.conn.execute("DELETE FROM catalogue_models WHERE ref=?", (mref,))
        self.cat.conn.commit()
        fake = {name: {"input_cost_per_token": 1e-8,
                       "output_cost_per_token": 2e-8,
                       "max_input_tokens": 8000}}
        s = populate_provider_models(self.cat, fake, dry_run=False)
        self.assertEqual(s["links_created"], 1)
        self.assertEqual(s["models_created"], 1)
        r = self.cat.conn.execute(
            """SELECT pm.cost_per_input_token FROM provider_models pm
               JOIN catalogue_providers p ON p.id=pm.provider_id
               WHERE p.ref='groq' AND pm.provider_model_name=?""", (name,)).fetchone()
        self.assertIsNotNone(r)
        self.assertEqual(r["cost_per_input_token"], "1e-08")
        # cleanup
        self.cat.conn.execute("DELETE FROM provider_models WHERE provider_model_name=?", (name,))
        self.cat.conn.execute("DELETE FROM catalogue_models WHERE ref=?", (mref,))
        self.cat.conn.commit()

    def test_populate_does_not_overwrite_existing_cost(self):
        name = "groq/populatetest-llama-x"
        mref = "populatetest-llama-x"
        # cree un modele + lien groq avec un cout deja present
        self.cat.conn.execute("DELETE FROM provider_models WHERE provider_model_name=?", (name,))
        self.cat.conn.execute("DELETE FROM catalogue_models WHERE ref=?", (mref,))
        self.cat.conn.commit()
        pid = self.cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref='groq'").fetchone()[0]
        self.cat.conn.execute(
            "INSERT INTO catalogue_models (ref, name, developer) VALUES (?,?,?)",
            (mref, mref, "groq"))
        mid = self.cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref=?", (mref,)).fetchone()[0]
        self.cat.conn.execute(
            """INSERT INTO provider_models
               (provider_id, model_id, provider_model_name, cost_per_input_token)
               VALUES (?,?,?, '1e-09')""", (pid, mid, name))
        self.cat.conn.commit()
        fake = {name: {"input_cost_per_token": 9.9e-7,
                       "output_cost_per_token": 9.9e-7,
                       "max_input_tokens": 12345}}
        s = populate_provider_models(self.cat, fake, dry_run=False)
        r = self.cat.conn.execute(
            "SELECT cost_per_input_token, context_window_tokens FROM provider_models "
            "WHERE provider_model_name=?", (name,)).fetchone()
        # cout EXISTANT 1e-09 conserve (pas ecrase par 9.9e-7)
        self.assertEqual(r["cost_per_input_token"], "1e-09")
        # context vide avant -> rempli par le populate (comportement additif attendu)
        self.assertEqual(r["context_window_tokens"], 12345)
        self.assertGreaterEqual(s["links_updated"], 1)
        # cleanup
        self.cat.conn.execute("DELETE FROM provider_models WHERE provider_model_name=?", (name,))
        self.cat.conn.execute("DELETE FROM catalogue_models WHERE ref=?", (mref,))
        self.cat.conn.commit()


if __name__ == "__main__":
    unittest.main()
