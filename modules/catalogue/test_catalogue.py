import unittest
import json
import shutil
from pathlib import Path
from modules.catalogue.catalogue import Catalogue

class TestCatalogue(unittest.TestCase):
    def setUp(self):
        self.test_data_dir = Path(__file__).parent / "test_data"
        self.test_data_dir.mkdir(exist_ok=True)
        self.cat = Catalogue(data_dir=self.test_data_dir)

    def tearDown(self):
        if self.test_data_dir.exists():
            shutil.rmtree(self.test_data_dir)

    def test_add_get_provider(self):
        prov = {"id": "p1", "name": "P1", "type": "cloud"}
        self.cat.add_provider(prov)
        self.assertEqual(self.cat.get_provider("p1"), prov)
        
        with self.assertRaises(ValueError):
            self.cat.add_provider(prov)

    def test_add_get_model(self):
        self.cat.add_provider({"id": "p1", "name": "P1", "type": "cloud"})
        model = {"id": "m1", "provider_id": "p1", "name": "M1", "is_chat_model": True}
        self.cat.add_model(model)
        self.assertEqual(self.cat.get_model("m1"), model)
        
        with self.assertRaises(ValueError):
            self.cat.add_model(model)

    def test_update_model(self):
        self.cat.add_provider({"id": "p1", "name": "P1", "type": "cloud"})
        model = {"id": "m1", "provider_id": "p1", "name": "M1", "is_chat_model": True}
        self.cat.add_model(model)
        self.cat.update_model("m1", {"name": "M1-Updated"})
        self.assertEqual(self.cat.get_model("m1")["name"], "M1-Updated")

    def test_get_models_by_provider(self):
        self.cat.add_provider({"id": "p1", "name": "P1", "type": "cloud"})
        self.cat.add_provider({"id": "p2", "name": "P2", "type": "cloud"})
        self.cat.add_model({"id": "m1", "provider_id": "p1", "name": "M1", "is_chat_model": True})
        self.cat.add_model({"id": "m2", "provider_id": "p1", "name": "M2", "is_chat_model": True})
        self.cat.add_model({"id": "m3", "provider_id": "p2", "name": "M3", "is_chat_model": True})
        
        p1_models = self.cat.get_models_by_provider("p1")
        self.assertEqual(len(p1_models), 2)
        self.assertEqual(p1_models[0]["id"], "m1")

if __name__ == "__main__":
    unittest.main()
