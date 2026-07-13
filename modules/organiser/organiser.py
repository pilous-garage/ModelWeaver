import sys
from typing import Any, Dict, List, Optional
from modules.catalogue.catalogue import Catalogue
from modules.key_manager.key_manager import KeyManager

class Organiser:
    def __init__(self, catalogue: Catalogue, key_manager: KeyManager):
        self.catalogue = catalogue
        self.key_manager = key_manager

    def start_interactive_menu(self):
        """Starts a simple terminal-based interactive menu."""
        print("--- ModelWeaver Organiser (Terminal Edition) ---")
        print("1. View available models")
        print("2. Add a provider key")
        print("3. Exit")
        
        choice = input("Choose an option: ")
        
        if choice == "1":
            self.list_models()
        elif choice == "2":
            self.add_key_menu()
        elif choice == "3":
            print("Exiting...")
            sys.exit(0)
        else:
            print("Invalid choice.")

    def list_models(self):
        models = self.catalogue.get_models()
        if not models:
            print("No models found in catalogue. Try syncing first.")
            return
        
        print("\nAvailable Models:")
        for m in models:
            print(f"- {m['id']} ({m.get('provider_id', 'unknown')})")

    def add_key_menu(self):
        provider_id = input("Enter provider ID: ")
        api_key = input("Enter API key: ")
        self.key_manager.set_key(provider_id, api_key)
        print(f"Key for {provider_id} added.")

if __name__ == "__main__":
    # Quick test
    from pathlib import Path
    cat = Catalogue(data_dir=Path("test_cat_data"))
    km = KeyManager()
    organiser = Organiser(cat, km)
    # We don't want to actually start an interactive menu in a test, 
    # so we'll just call one of the methods.
    organiser.list_models()
    
    # Cleanup
    import shutil
    import os
    if Path("test_cat_data").exists(): shutil.rmtree("test_cat_data")
    if Path("test_km_vault.json").exists(): os.remove("test_km_vault.json")
