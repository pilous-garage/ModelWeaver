import unittest
from pathlib import Path

from modules.sql.db import CatalogueDB
from modules.catalogue.openrouter import merge_openrouter, _ensure_provider


class TestMergeOpenRouter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import tempfile, os
        fd, path = tempfile.mkstemp(suffix=".db", prefix="mw_or_test_")
        os.close(fd)
        os.unlink(path)
        cls.db_path = Path(path)
        cls.cat = CatalogueDB(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()
        try:
            cls.db_path.unlink()
        except Exception:
            pass

    def _make_data(self):
        # 2 modeles openai (1 payant, 1 free), 1 provider absent (qwen) free
        return {
            "openai/gpt-4o": {
                "id": "openai/gpt-4o", "name": "gpt-4o", "provider": "openai",
                "cost_per_input_token": "5.000000e-06",
                "cost_per_output_token": "1.500000e-05",
                "context_window_tokens": 128000, "max_output_tokens": None,
                "per_request_limits": None, "is_free": False,
            },
            "openai/gpt-4o-mini:free": {
                "id": "openai/gpt-4o-mini:free", "name": "gpt-4o-mini free",
                "provider": "openai",
                "cost_per_input_token": None, "cost_per_output_token": None,
                "context_window_tokens": 128000, "max_output_tokens": None,
                "per_request_limits": None, "is_free": True,
            },
            "qwen/qwen2.5-7b:free": {
                "id": "qwen/qwen2.5-7b:free", "name": "qwen2.5-7b", "provider": "qwen",
                "cost_per_input_token": None, "cost_per_output_token": None,
                "context_window_tokens": 32000, "max_output_tokens": None,
                "per_request_limits": None, "is_free": True,
            },
        }

    def test_merge_creates_provider_and_marks_free(self):
        data = self._make_data()
        s = merge_openrouter(self.cat, data, dry_run=False)
        self.assertGreaterEqual(s["created"], 1)  # qwen provider/model crees
        self.assertGreaterEqual(s["providers_created"], 1)
        # openai/gpt-4o enrichi (cout non-nul ; valeur exacte depend du seed litellm
        # deja present, le merge est additif et n'ecrase pas un cout existant)
        r = self.cat.conn.execute(
            "SELECT pm.cost_per_input_token, pm.free_tier FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id "
            "JOIN catalogue_models m ON m.id=pm.model_id "
            "WHERE p.ref='openai' AND m.ref='gpt-4o'").fetchone()
        self.assertIsNotNone(r)
        self.assertIsNotNone(r["cost_per_input_token"])
        self.assertEqual(r["free_tier"], 0)
        # free marque
        n = self.cat.conn.execute(
            "SELECT COUNT(*) FROM provider_models pm "
            "JOIN catalogue_providers p ON p.id=pm.provider_id "
            "WHERE p.ref='openai' AND pm.provider_model_name LIKE '%:free' "
            "AND pm.free_tier=1").fetchone()[0]
        self.assertGreaterEqual(n, 1)
        # qwen present
        q = self.cat.conn.execute(
            "SELECT COUNT(*) FROM catalogue_providers WHERE ref='qwen'").fetchone()[0]
        self.assertEqual(q, 1)

    def test_merge_idempotent(self):
        data = self._make_data()
        s1 = merge_openrouter(self.cat, data, dry_run=False)
        s2 = merge_openrouter(self.cat, data, dry_run=False)
        # second passage : pas de nouvelles creations (OR IGNORE / update)
        self.assertEqual(s2["created"], 0)


if __name__ == "__main__":
    unittest.main()
