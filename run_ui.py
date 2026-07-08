import sys
from pathlib import Path

# Ajouter le répertoire courant au sys.path pour permettre l'import des modules
sys.path.append(str(Path(__file__).resolve().parent))

from modules.catalogue.catalogue import Catalogue
from modules.key_manager.key_manager import KeyManager
from modules.checker.checker import Checker
from modules.organiser.organiser import Organiser
from modules.dashboard.dashboard import Dashboard

def main():
    print("🚀 ModelWeaver UI Launcher")
    print("---------------------------")
    print("1. Lancer l'Organiser (Menu interactif)")
    print("2. Lancer le Dashboard (Monitoring)")
    print("3. Quitter")
    
    choice = input("\nVotre choix : ").strip()

    # Initialisation des composants avec les chemins par défaut (réels)
    cat = Catalogue()
    km = KeyManager()
    checker = Checker()

    if choice == "1":
        organiser = Organiser(cat, km)
        organiser.start_interactive_menu()
    elif choice == "2":
        dashboard = Dashboard(checker, cat)
        try:
            dashboard.monitor_loop(interval=5)
        except KeyboardInterrupt:
            print("\nDashboard arrêté.")
    elif choice == "3":
        print("Au revoir !")
        sys.exit(0)
    else:
        print("Choix invalide.")

if __name__ == "__main__":
    main()
