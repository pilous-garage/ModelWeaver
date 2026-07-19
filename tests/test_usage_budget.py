import unittest
import tempfile
import os
import time
from pathlib import Path

from modules.sql.db import ModelWeaverDB, CatalogueDB
from modules.catalogue.pricing import fetch_litellm_pricing, merge_pricing
from modules.usage.usage_collector import _record_budget
from modules.usage.budget import (
    get_budget_summary, get_free_tier_models, get_budget_rows)


class TestFreeTierMarking(unittest.TestCase):
    """Le merge pricing doit marquer free_tier=1 pour les modeles a cout nul."""

    @classmethod
    def setUpClass(cls):
        cls.cat = CatalogueDB()

    @classmethod
    def tearDownClass(cls):
        cls.cat.close()

    def test_free_tier_marked(self):
        lit = fetch_litellm_pricing()
        merge_pricing(self.cat, lit, dry_run=False)
        n = self.cat.conn.execute(
            "SELECT COUNT(*) FROM provider_models WHERE free_tier=1").fetchone()[0]
        self.assertGreater(n, 0)
        # un modele avec cout doit etre marque non-free
        paid = self.cat.conn.execute(
            "SELECT COUNT(*) FROM provider_models "
            "WHERE free_tier=0 AND cost_per_input_token IS NOT NULL "
            "AND cost_per_input_token <> '0.0'").fetchone()[0]
        self.assertGreater(paid, 0)


class TestBudgetConsolidation(unittest.TestCase):
    FLAG_PROV = "__budget_test_prov__"
    FLAG_MODEL = "__budget_test_model__"

    @classmethod
    def setUpClass(cls):
        fd, path = tempfile.mkstemp(suffix=".db", prefix="mw_budget_test_")
        os.close(fd)
        os.unlink(path)
        cls.db_path = Path(path)
        cls.mw = ModelWeaverDB(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        cls.mw.close()
        try:
            cls.db_path.unlink()
        except Exception:
            pass

    def _reset(self):
        self.mw.conn.execute(
            "DELETE FROM budget_consumption WHERE budget_id IN "
            "(SELECT id FROM really_used_budget WHERE target_ref LIKE '__budget_test%')")
        self.mw.conn.execute(
            "DELETE FROM really_used_budget WHERE target_ref LIKE '__budget_test%'")
        self.mw.conn.commit()

    def test_budget_accumulates(self):
        self._reset()
        _record_budget(self.mw.conn, self.FLAG_PROV, self.FLAG_MODEL, "agentX",
                       1000, 500, 0.0001)
        _record_budget(self.mw.conn, self.FLAG_PROV, self.FLAG_MODEL, "agentX",
                       1000, 500, 0.0002)
        s = get_budget_summary(self.mw)
        self.assertAlmostEqual(s["by_provider"].get(self.FLAG_PROV, 0.0), 0.0003, places=8)
        self.assertAlmostEqual(s["by_model"].get(self.FLAG_MODEL, 0.0), 0.0003, places=8)
        self.assertAlmostEqual(s["by_agent"].get("agentX", 0.0), 0.0003, places=8)
        self._reset()

    def test_budget_rows(self):
        self._reset()
        _record_budget(self.mw.conn, self.FLAG_PROV, self.FLAG_MODEL, None,
                       10, 10, 0.001)
        rows = get_budget_rows(self.mw, limit=10)
        self.assertTrue(any(r["target_ref"] == self.FLAG_PROV for r in rows))
        self._reset()

    def test_sweep_agent_actif_ttl(self):
        # insere un agent avec heartbeat tres ancien et un recent
        now = int(time.time())
        self.mw.conn.execute(
            "INSERT OR REPLACE INTO agent_actif (agent_id, status, last_heartbeat) "
            "VALUES ('__old__', 'running', ?)", (now - 999999,))
        self.mw.conn.execute(
            "INSERT OR REPLACE INTO agent_actif (agent_id, status, last_heartbeat) "
            "VALUES ('__new__', 'running', ?)", (now,))
        self.mw.conn.commit()
        from modules.usage.usage_collector import _sweep_agent_actif_ttl
        n = _sweep_agent_actif_ttl(self.mw)
        self.assertGreaterEqual(n, 1)
        old = self.mw.conn.execute(
            "SELECT COUNT(*) FROM agent_actif WHERE agent_id='__old__'").fetchone()[0]
        new = self.mw.conn.execute(
            "SELECT COUNT(*) FROM agent_actif WHERE agent_id='__new__'").fetchone()[0]
        self.assertEqual(old, 0)
        self.assertEqual(new, 1)
        self.mw.conn.execute("DELETE FROM agent_actif WHERE agent_id IN ('__old__','__new__')")
        self.mw.conn.commit()


if __name__ == "__main__":
    unittest.main()
