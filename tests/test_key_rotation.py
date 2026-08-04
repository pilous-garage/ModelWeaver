import unittest
import time
import json
from modules.sql.db import ModelWeaverDB
from modules.key_rotation.rotation_module import KeyRotationService
from services.key_rotation_service import KeyRotationREST

class TestKeyRotation(unittest.TestCase):
    def setUp(self):
        self.db = ModelWeaverDB()
        # Créer un provider et une clé de test
        self.prov_id = self.db.providers.save({
            "ref": "test_provider",
            "name": "Test Provider",
            "provider_type": "cloud"
        })
        self.key_ref = self.db.keys.save({
            "ref": "key_test_123",
            "provider_id": self.prov_id,
            "key_value": "sk-test-original",
            "tag": "paid",
            "metadata_json": json.dumps({"rotation_interval_days": 0}) # Force expiration immédiate pour test
        })

    def tearDown(self):
        self.db.keys.delete(self.key_ref)
        self.db.providers.delete("test_provider")
        self.db.close()

    def test_rotate_key(self):
        service = KeyRotationService(db=self.db)
        res = service.rotate_key(self.key_ref, new_key_material="sk-test-new-material", grace_period_hours=1)
        self.assertEqual(res["status"], "ok")
        self.assertIn("new_key_ref", res)

    def test_check_and_rotate(self):
        service = KeyRotationService(db=self.db)
        res = service.check_and_rotate_expired()
        self.assertEqual(res["status"], "ok")

if __name__ == "__main__":
    unittest.main()
