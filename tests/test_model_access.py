"""Test : classification d'acces aux modeles (avec cle / sans cle)."""

import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.sql.db import CatalogueDB
import scripts.sync_provider_models as spm


class TestModelAccess(unittest.TestCase):
    def setUp(self):
        self.cat = CatalogueDB()
        # nettoyage des lignes de test
        self.cat.conn.execute("DELETE FROM key_endpoint_models WHERE key_ref IN ('', 'key_test123')")
        self.cat.conn.commit()
        # provider + endpoint + modele de test
        self.cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_providers (ref,name,provider_type) "
            "VALUES ('tprov','Test','cloud')")
        self.pid = self.cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref='tprov'").fetchone()[0]
        self.cat.conn.execute(
            "INSERT OR IGNORE INTO provider_endpoints (provider_id,label,endpoint_url,is_default) "
            "VALUES (?, 'v1', 'http://x', 1)", (self.pid,))
        self.eid = self.cat.conn.execute(
            "SELECT endpoint_id FROM provider_endpoints WHERE provider_id=?",
            (self.pid,)).fetchone()[0]
        self.cat.conn.execute(
            "INSERT OR IGNORE INTO catalogue_models (ref,name,developer) "
            "VALUES ('dev/m1','m1','dev')")
        self.m1 = self.cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref='dev/m1'").fetchone()[0]

    def tearDown(self):
        self.cat.conn.execute("DELETE FROM key_endpoint_models WHERE key_ref IN ('', 'key_test123')")
        self.cat.conn.execute("DELETE FROM catalogue_providers WHERE ref='tprov'")
        self.cat.conn.commit()
        self.cat.close()

    def _insert(self, key_ref, declared=1, available=1):
        self.cat.conn.execute(
            "INSERT OR REPLACE INTO key_endpoint_models "
            "(provider_id, endpoint_id, key_ref, model_id, provider_model_name, declared, available) "
            "VALUES (?,?,?,?,?,?,?)",
            (self.pid, self.eid, key_ref, self.m1, "m1", declared, available))

    def test_public_vs_keyed(self):
        # sans cle -> KEY_REF_PUBLIC ('')
        self._insert(spm.KEY_REF_PUBLIC)
        # avec cle -> vraie ref
        self._insert("key_test123")
        by = spm.get_models_by_access(self.cat, provider="tprov")
        self.assertEqual(len(by["public"]), 1)
        self.assertEqual(len(by["with_key"]), 1)
        self.assertEqual(by["public"][0]["key_ref"], "")
        self.assertEqual(by["with_key"][0]["key_ref"], "key_test123")

    def test_declared_filter_excludes_stale(self):
        # ligne stale (declared=0) ne doit pas apparaitre dans la classification
        self._insert("key_test123", declared=0, available=1)
        by = spm.get_models_by_access(self.cat, provider="tprov")
        self.assertEqual(len(by["with_key"]), 0)
        self.assertEqual(len(by["public"]), 0)


if __name__ == "__main__":
    unittest.main()
