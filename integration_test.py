import os
import sys
import shutil
import unittest
from pathlib import Path

# Add current directory to sys.path to ensure modules can be imported
sys.path.append(str(Path(__file__).resolve().parent))

from modules.catalogue.catalogue import Catalogue
from modules.key_manager.key_manager import KeyManager
from modules.key_manager.onboarder import Onboarder
from modules.checker.checker import Checker
from modules.installer.installer import Installer
from modules.container_manager.container_manager import ContainerManager
from modules.test_runner.test_runner import TestRunner
from modules.plumber.plumber import Plumber
from modules.organiser.organiser import Organiser
from modules.dashboard.dashboard import Dashboard

class TestAssembly(unittest.TestCase):
    def setUp(self):
        # Setup temporary directories for testing
        self.base_dir = Path(__file__).resolve().parent / "test_assembly_data"
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)
        self.base_dir.mkdir(parents=True)
        
        self.cat_dir = self.base_dir / "catalogue"
        self.cat_dir.mkdir()
        self.cat = Catalogue(data_dir=self.cat_dir)
        
        self.km_vault = self.base_dir / "vault.json"
        self.km = KeyManager(vault_path=self.km_vault)
        
        self.checker = Checker(state_file=self.base_dir / "state.json")
        
        self.installer = Installer()
        
        self.cm = ContainerManager()
        self.tr = TestRunner(self.cm)
        
        self.plumber = Plumber(self.cat, self.km)
        
        self.organiser = Organiser(self.cat, self.km)
        
        self.dashboard = Dashboard(self.checker, self.cat)

    def tearDown(self):
        if self.base_dir.exists():
            shutil.rmtree(self.base_dir)

    def test_full_workflow(self):
        print("\n--- Starting Integration Test ---")
        
        # 1. Catalogue Sync (Simulation)
        print("Step 1: Catalogue Simulation...")
        self.cat.add_provider({"id": "test_p", "name": "Test P", "type": "cloud"})
        self.cat.add_model({"id": "test_m", "provider_id": "test_p", "name": "Test M", "is_chat_model": True})
        self.cat.save()
        self.assertEqual(len(self.cat.get_models()), 1)
        print("✅ Catalogue simulation ok.")

        # 2. Key Management & Onboarding
        print("Step 2: Key Management & Onboarding...")
        env_file = self.base_dir / ".env.test"
        env_file.write_text("GROQ_API_KEY=gsk-test-456")
        
        onboarder = Onboarder(self.km, catalogue=self.cat)
        found = onboarder.onboard_from_env(env_file)
        self.assertGreater(found, 0)
        self.assertIsNotNone(self.km.get_key("groq"))
        print("✅ Key onboarding ok.")

        # 3. System Check
        print("Step 3: System Check...")
        state = self.checker.run_all_checks()
        self.assertIn("system", state)
        self.assertIn("hardware", state)
        self.assertIn("dependencies", state)
        print("✅ System check ok.")

        # 4. Container & Test Runner
        print("Step 4: Container & Test Runner...")
        script = "print('Integration success')"
        result = self.tr.run_test_script(script)
        self.assertTrue(result["success"])
        self.assertIn("Integration success", result["output"])
        print("✅ Container test ok.")

        # 5. Plumber (Placeholder check)
        print("Step 5: Plumber (Placeholder)...")
        route = self.plumber.route_request("hi")
        self.assertEqual(route["status"], "not_implemented")
        print("✅ Plumber placeholder ok.")

        # 6. Dashboard (Visual check)
        print("Step 6: Dashboard...")
        # Just ensure it doesn't crash
        self.dashboard.show_status()
        print("✅ Dashboard ok.")

        print("\n--- Integration Test PASSED ---")

if __name__ == "__main__":
    unittest.main()
