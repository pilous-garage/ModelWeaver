"""Test : gestionnaire de config global + borne agent_actif (FIFO)."""

import os
import sys
import threading
import time
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.config.config_manager import ConfigManager
from modules.sql.db import ModelWeaverDB


class TestConfigManager(unittest.TestCase):
    def setUp(self):
        # instance propre + config.json vide pour le test
        self.cfg = ConfigManager()
        # reset aux defaults (le singleton persiste entre tests de la meme classe)
        from modules.config.config_manager import DEFAULTS
        with self.cfg._lock:
            self.cfg._data = dict(DEFAULTS)
        try:
            self.cfg._path.write_text("{}", encoding="utf-8")
        except Exception:
            pass
        self.cfg.reload()

    def test_defaults(self):
        self.assertEqual(self.cfg.get("usage.ram_buffer_bytes"), 10 * 1024 * 1024)
        self.assertEqual(self.cfg.get("usage.agent_actif_max_rows"), 1000)

    def test_set_persist_and_hot_reload_callback(self):
        self.cfg.set("usage.ram_buffer_bytes", 20 * 1024 * 1024)
        self.assertEqual(self.cfg.get("usage.ram_buffer_bytes"), 20 * 1024 * 1024)
        # reload depuis disque renvoie la meme valeur (simule GUI externe)
        self.cfg.reload()
        self.assertEqual(self.cfg.get("usage.ram_buffer_bytes"), 20 * 1024 * 1024)
        # callback notifie
        fired = []
        self.cfg.register_callback(lambda: fired.append(1))
        self.cfg.set("usage.collector_poll_seconds", 3.0)
        self.assertEqual(fired, [1])

    def test_unknown_key_falls_back_to_default(self):
        self.assertEqual(self.cfg.get("usage.nope", 42), 42)
        # cle inconnue absente des defaults -> default fourni
        self.assertIsNone(self.cfg.get("totally.unknown"))


class TestAgentActifCap(unittest.TestCase):
    def setUp(self):
        m = ModelWeaverDB()
        m.conn.execute("DELETE FROM agent_actif")
        m.conn.commit()
        m.close()
        self.cfg = ConfigManager()
        from modules.config.config_manager import DEFAULTS
        with self.cfg._lock:
            self.cfg._data = dict(DEFAULTS)
        try:
            self.cfg._path.write_text("{}", encoding="utf-8")
        except Exception:
            pass
        self.cfg.reload()

    def test_fifo_cap_enforced(self):
        from modules.usage import usage_collector
        # borne petite pour le test
        self.cfg.set("usage.agent_actif_max_rows", 3)
        m = ModelWeaverDB()
        now = int(time.time())
        for i in range(6):
            m.conn.execute(
                "INSERT INTO agent_actif (agent_id, last_heartbeat, calls_count, tokens_total) "
                "VALUES (?, ?, 1, 0)", (f"a{i}", now + i))
        m.conn.commit()
        m.close()
        # le rassembleur applique la borne
        usage_collector._enforce_agent_actif_cap(ModelWeaverDB())
        m = ModelWeaverDB()
        rows = [r["agent_id"] for r in m.conn.execute(
            "SELECT agent_id FROM agent_actif ORDER BY last_heartbeat").fetchall()]
        m.close()
        # 6 inseres, cap 3 -> seuls les 3 plus recents (a3,a4,a5) survivent
        self.assertEqual(rows, ["a3", "a4", "a5"])
        self.assertEqual(len(rows), 3)


if __name__ == "__main__":
    unittest.main()
