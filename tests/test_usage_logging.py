"""Test : journalisation usage disque + rassembleur, 0 perte sous concurrence.

Valide :
  - _log_call du bridge ecrit sur disque (pas de lock SQLite).
  - le rassembleur consolide sans perte meme sous charge concurrente.
  - calcul de cout USD via provider_models.
"""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.usage import usage_log, usage_collector
from modules.sql.db import ModelWeaverDB, CatalogueDB


def _clear_logs():
    d = Path(os.path.expanduser("~")) / ".modelweaver" / usage_log.USAGE_DIR
    for p in d.glob(usage_log.LOG_FILENAME + "*"):
        try:
            p.unlink()
        except Exception:
            pass
    usage_log.reset_path_cache()


class TestUsageLogging(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _clear_logs()
        # reset tables cibles
        m = ModelWeaverDB()
        m.conn.execute("DELETE FROM real_call_models")
        m.conn.execute("DELETE FROM endpoint_model_usage")
        m.conn.execute("DELETE FROM agent_actif")
        m.conn.commit()
        m.close()

    @classmethod
    def tearDownClass(cls):
        _clear_logs()

    def test_disk_append_no_sqlite_lock(self):
        """100 appends concurrents : aucun ne doit echouer (pas de lock)."""
        _clear_logs()
        errors = []

        def worker(i):
            try:
                ok = usage_log.log_call(
                    "groq", "groq/llama-3.1-8b-instant", "ok",
                    agent_id="w%d" % i, tokens_in=5, tokens_out=10)
                if not ok:
                    errors.append(i)
            except Exception as e:
                errors.append((i, str(e)))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(100)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(errors, [], "des appends ont echoue")
        # le fichier contient bien 100 lignes
        p = usage_log.log_path()
        with open(p) as f:
            lines = [l for l in f if l.strip()]
        self.assertEqual(len(lines), 100, "lignes manquantes sur disque")

    def test_collector_zero_loss(self):
        """Concurrence append + consolidation : 0 perte totale."""
        _clear_logs()
        m = ModelWeaverDB()
        m.conn.execute("DELETE FROM real_call_models")
        m.conn.execute("DELETE FROM endpoint_model_usage")
        m.conn.commit()
        m.close()

        N = 200
        stop = False
        collected_total = [0]

        def appender():
            for i in range(N):
                usage_log.log_call(
                    "groq", "groq/llama-3.1-8b-instant", "ok",
                    agent_id="w%d" % (i % 4), tokens_in=7, tokens_out=13)

        def collector_loop():
            while not stop:
                try:
                    usage_collector.run_once()
                except Exception:
                    pass
                time.sleep(0.01)

        ap = threading.Thread(target=appender)
        co = threading.Thread(target=collector_loop)
        ap.start()
        co.start()
        ap.join()
        # laisse le collector finir de vider
        time.sleep(0.5)
        stop = True
        co.join()

        # vidage final
        usage_collector.run_once()

        m = ModelWeaverDB()
        rows = m.conn.execute("SELECT COUNT(*) FROM real_call_models").fetchone()[0]
        m.close()
        self.assertEqual(rows, N, "perte : %d/%d lignes en base" % (rows, N))

    def test_cost_computed(self):
        """Le cout USD est deduit de provider_models si absent du record."""
        cat = CatalogueDB()
        # groq/llama-3.1-8b-instant : on force un tarif connu pour le test
        cat.conn.execute("""
            UPDATE provider_models SET
                cost_per_input_token = '0.0001',
                cost_per_output_token = '0.0002'
            WHERE model_id = (SELECT id FROM catalogue_models WHERE ref='groq/llama-3.1-8b-instant')
              AND provider_id = (SELECT id FROM catalogue_providers WHERE ref='groq')
        """)
        cat.conn.commit()
        cost = usage_collector._cost_for(cat, "groq", "groq/llama-3.1-8b-instant",
                                         tokens_in=100, tokens_out=50)
        cat.close()
        expected = 100 * 0.0001 + 50 * 0.0002
        self.assertAlmostEqual(cost, expected, places=6)


if __name__ == "__main__":
    unittest.main()
