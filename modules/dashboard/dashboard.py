import time
from typing import Any, Dict
from modules.checker.checker import Checker
from modules.catalogue.catalogue import Catalogue

class Dashboard:
    def __init__(self, checker: Checker, catalogue: Catalogue):
        self.checker = checker
        self.catalogue = catalogue

    def show_status(self):
        """Shows the current system and catalogue status."""
        print("\n=== ModelWeaver Dashboard ===")
        
        # System status
        print("\n[System Status]")
        state = self.checker.run_all_checks()
        print(f"OS: {state['system']['os']} {state['system']['os_release']}")
        print(f"RAM: {state['hardware']['ram_available_gb']} GB available / {state['hardware']['ram_total_gb']} GB total")
        
        # Dependencies
        print("\n[Dependencies]")
        for dep in state['dependencies']:
            status = "✅" if dep['present'] else "❌"
            print(f"{status} {dep['name']} ({dep['path'] or 'Not found'})")
            
        # Catalogue status
        print("\n[Catalogue Status]")
        models = self.catalogue.get_models()
        print(f"Total models: {len(models)}")
        
        if models:
            print("Top 5 models:")
            for m in models[:5]:
                print(f"  - {m['id']}")

    def monitor_loop(self, interval: int = 5):
        """Monitors the system in a loop."""
        try:
            while True:
                self.show_status()
                print(f"\nRefreshing in {interval}s... (Ctrl+C to stop)")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\nDashboard stopped.")

if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    cat = Catalogue(data_dir=Path("test_cat_data"))
    checker = Checker(state_file=Path("test_state.json"))
    dashboard = Dashboard(checker, cat)
    dashboard.show_status()
    
    # Cleanup
    import os
    import shutil
    if Path("test_cat_data").exists(): shutil.rmtree("test_cat_data")
    if Path("test_state.json").exists(): os.remove("test_state.json")
