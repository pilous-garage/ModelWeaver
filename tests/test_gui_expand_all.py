#!/usr/bin/env python3
"""Test GUI : ouvre le sandbox, sélectionne l'agent worker, et clique 'Tout déplier'.

Pré-requis :
    - Daemon en cours d'exécution (port 8770 ou 8771)
    - `npm run tauri dev` ou `npm run dev` (Vite) sur http://localhost:5173
    - playwright installé (pip install playwright && playwright install chromium)

Usage :
    PYTHONPATH=. python3 tests/test_gui_expand_all.py [--headless] [--url URL]
"""

import os
import sys
import time
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.gui_autoclick import TauriAutoClicker


def main():
    parser = argparse.ArgumentParser(description="Test GUI expand all")
    parser.add_argument("--headless", action="store_true", help="Mode headless")
    parser.add_argument("--url", default="http://localhost:5173", help="URL du dev server")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout max en secondes")
    args = parser.parse_args()

    ac = TauriAutoClicker(url=args.url, headless=args.headless, timeout=args.timeout * 1000)

    try:
        print(f"🚀 Démarrage du test — {args.url}")
        ac.start()
        print("✅ Page chargée")

        # Connexion — l'appli peut demander un onboard, on skip
        if "onboard" in ac.page.url.lower():
            print("🔄 Onboard détecté, skip...")
            ac.click_text("Ignorer")

        # Naviguer vers le sandbox
        print("📂 Ouverture du sandbox...")
        ac.switch_to_sandbox()
        ac.wait(1)

        # Ouvrir l'agent worker en vue graphe
        print("🤖 Sélection du worker...")
        ac.open_agent_graph("worker")
        ac.wait(1)

        # Récupérer les logs console avant clic
        logs_before = ac.eval("() => { const c = []; "
                               "const orig = console.log; "
                               "console.log = (...a) => { c.push(a.join(' ')); orig.apply(console, a); }; "
                               "return c; }")

        # Cliquer sur Tout déplier
        print("⤢ Clic sur Tout déplier...")
        ac.expand_all()

        # Attendre que les requêtes asynchrones se terminent
        print("⏳ Attente des injections...")
        ac.wait(5)

        # Screenshot final
        path = ac.screenshot("expand_all_result.png")
        print(f"📸 Screenshot sauvegardé : {path}")

        # Récupérer les logs
        final_logs = ac.text("body")
        print(f"📝 Page text (200 premiers chars): {final_logs[:200]}")

        # Vérifier le nombre de nœuds
        node_count = ac.eval("document.querySelectorAll('.react-flow__node').length")
        edge_count = ac.eval("document.querySelectorAll('.react-flow__edge').length")
        print(f"📊 Nœuds: {node_count}, Arêtes: {edge_count}")

        print("✅ Test terminé avec succès")
        return 0

    except Exception as e:
        print(f"❌ Échec du test : {e}")
        try:
            ac.screenshot("expand_all_error.png")
            print("📸 Screenshot d'erreur sauvegardé")
        except Exception:
            pass
        return 1
    finally:
        ac.close()


if __name__ == "__main__":
    sys.exit(main())
