#!/usr/bin/env python3
"""Smoke test sans Playwright — enchaîne des clics simples dans le navigateur.

Pré-requis :
    - `npm run tauri dev` (ou `npm run dev`) sur http://localhost:5173
    - Daemon sur 8770 ou 8771 (via tauri dev)

Usage :
    python3 tests/smoke_click.py [--headless] [--url URL]

Ce script :
    1. Attend 15s pour stabilisation
    2. Navigue vers le sandbox (?sandbox)
    3. Ouvre l'agent worker en vue graphe
    4. Clique "Tout déplier"
    5. Sauvegarde le HTML complet dans /tmp/smoke_graph.html
       + dump les nœuds/arêtes en JSON dans /tmp/smoke_nodes.json
"""

import os, sys, time, json, argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.gui_autoclick import TauriAutoClicker


def main():
    parser = argparse.ArgumentParser(description="Smoke click — test de boutons")
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--url", default="http://localhost:5173")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    ac = TauriAutoClicker(url=args.url, headless=args.headless,
                          timeout=args.timeout * 1000)
    try:
        print("⏳ Attente 15s stabilisation taureau...")
        time.sleep(15)

        print("🌐 Navigation vers sandbox...")
        ac.start()
        print(f"   URL = {ac.page.url}")

        print("📂 switch_to_sandbox()")
        ac.switch_to_sandbox()
        ac.wait(2)

        print("🤖 open_agent_graph('worker')")
        ac.open_agent_graph("worker")
        ac.wait(2)

        # Vérif que le graphe est bien affiché
        body = ac.text("body")
        if "🔀" in body or "Graphe" in body:
            print("   ✅ vue graphe confirmée")
        else:
            print("   ⚠️  vue graphe non détectée :")
            print(body[:300])

        print("⤢ expand_all()")
        ac.expand_all()
        ac.wait(6)

        # Dump HTML
        html = ac.html("body")
        Path("/tmp/smoke_graph.html").write_text(html, encoding="utf-8")
        print(f"   📄 HTML sauvé → /tmp/smoke_graph.html ({len(html)} chars)")

        # Dump nodes JSON
        nodes_json = ac.eval("""() => {
            var nodes = [];
            document.querySelectorAll('.react-flow__node').forEach(function(n) {
                var label = (n.querySelector('div') || n).textContent.trim().substring(0,80);
                var dataId = n.getAttribute('data-id') || '';
                nodes.push({ id: dataId, label: label });
            });
            var edges = [];
            document.querySelectorAll('.react-flow__edge').forEach(function(e) {
                edges.push({
                    from: e.getAttribute('data-sourceid') || '',
                    to: e.getAttribute('data-targetid') || ''
                });
            });
            return JSON.stringify({ nodes: nodes, edges: edges });
        }""")
        Path("/tmp/smoke_nodes.json").write_text(nodes_json, encoding="utf-8")
        data = json.loads(nodes_json)
        print(f"   📊 nœuds={len(data['nodes'])}, arêtes={len(data['edges'])}  → /tmp/smoke_nodes.json")

        print("✅ Smoke test OK")
        return 0

    except Exception as e:
        print(f"❌ ÉCHEC : {e}")
        try:
            html = ac.html("body") if ac._page else ""
            if html:
                Path("/tmp/smoke_error.html").write_text(html, encoding="utf-8")
                print("   📄 HTML erreur → /tmp/smoke_error.html")
        except Exception:
            pass
        return 1
    finally:
        ac.close()


if __name__ == "__main__":
    sys.exit(main())