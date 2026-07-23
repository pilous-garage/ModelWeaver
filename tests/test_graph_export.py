#!/usr/bin/env python3
"""Test robuste : ouvre un agent en vue graphe, déplie tout, exporte le YAML.

Utilise RobustClicker avec timeout progressif (1→10min), retry automatique,
capture texte intégral pour debug, logs abondants.

Pré-requis :
    - Daemon sur 8770 (ou via --port)
    - Vite sur http://localhost:5173 (npm run dev ou tauri dev)
    - playwright installé (pip install playwright && playwright install chromium)

Usage :
    PYTHONPATH=. python3 tests/test_graph_export.py [--headless] [--url URL] [--port PORT]
"""

import os, sys, time, json, argparse, logging
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from tests.robust_clicker import RobustClicker, log

# ── Helper : kill daemon existant ──

def kill_daemon(port: int = 8770):
    """Tue tout process écoutant sur le port donné."""
    import subprocess
    log.info("🔪 Vérification port %d...", port)
    try:
        result = subprocess.run(
            ["fuser", f"{port}/tcp"],
            capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            pid = result.stdout.strip().split()[0]
            log.warning("   ⚠️  Port %d occupé par PID %s → kill", port, pid)
            subprocess.run(["fuser", "-k", f"{port}/tcp"], timeout=5)
            time.sleep(1)
            log.info("   ✅ Port %d libéré", port)
        else:
            log.info("   ✅ Port %d libre", port)
    except FileNotFoundError:
        log.warning("   ⚠️  fuser non trouvé (pas de auto-kill disponible)")
    except Exception as e:
        log.warning("   ⚠️  kill_daemon: %s", e)


# ── Test principal ──

def main():
    parser = argparse.ArgumentParser(description="Test export YAML du graphe (robuste)")
    parser.add_argument("--headless", action="store_true", help="Mode headless")
    parser.add_argument("--url", default="http://localhost:5173", help="URL Vite")
    parser.add_argument("--port", type=int, default=8770, help="Port du daemon")
    parser.add_argument("--output", default="/tmp/graph_export_test.yaml", help="Fichier YAML de sortie")
    parser.add_argument("--skip-daemon-kill", action="store_true", help="Ne pas tuer le daemon")
    args = parser.parse_args()

    output = Path(args.output)

    # Kill daemon existant sauf si skip
    if not args.skip_daemon_kill:
        kill_daemon(args.port)

    rc = RobustClicker(url=args.url, headless=args.headless)

    try:
        # ── 1. Navigation ──
        log.info("=" * 60)
        log.info("📋 TEST EXPORT YAML DU GRAPHE")
        log.info("=" * 60)
        rc.start()
        rc.switch_to_sandbox()

        # ── 2. Attente chargement BDD + catalogue ──
        rc.wait_for_catalogue_ready(timeout=120)

        # ── 3. Ouvrir agent worker en vue graphe ──
        rc.open_agent_graph("worker")

        # ── 4. Expand all ──
        rc.expand_all_graph()

        # ── 5. Compter nœuds / arêtes ──
        node_count = rc.count_graph_nodes()
        edge_count = rc.count_graph_edges()
        log.info("📊 Graphe: %d nœuds, %d arêtes", node_count, edge_count)
        if node_count == 0:
            log.warning("   ⚠️  Aucun nœud trouvé — capture debug")
            rc._debug_capture("no_nodes")
            # On continue quand même pour voir l'export

        # ── 6. Export YAML ──
        rc.export_graph_yaml(str(output))

        # ── 7. Validation ──
        yaml_content = output.read_text(encoding="utf-8")
        log.info("📄 YAML exporté: %d chars", len(yaml_content))

        assert "docId" in yaml_content, "docId manquant"
        assert "nodes" in yaml_content, "nodes manquant"
        assert "edges" in yaml_content, "edges manquant"
        log.info("   ✅ Structure YAML valide")

        # Vérifier cohérence
        import yaml
        parsed = yaml.safe_load(yaml_content)
        exported_nodes = len(parsed.get("nodes", []))
        exported_edges = len(parsed.get("edges", []))
        log.info("📊 Export: %d nœuds, %d arêtes", exported_nodes, exported_edges)

        if node_count > 0:
            assert exported_nodes >= node_count, \
                f"Export {exported_nodes} nœuds < attendu {node_count}"

        log.info("=" * 60)
        log.info("✅ TEST RÉUSSI")
        log.info("=" * 60)
        return 0

    except Exception as e:
        log.error("❌ ÉCHEC: %s", e)
        try:
            # Capture de texte intégral pour debug
            text = rc.get_page_text()
            log.info("📝 Dernier état de la page (%d chars):\n%s", len(text), text[:1000])
            # Sauver dans /tmp pour analyse
            Path("/tmp/graph_export_fail.txt").write_text(text, encoding="utf-8")
            log.info("   → Texte complet sauvé dans /tmp/graph_export_fail.txt")
            rc.screenshot("export_error.png")
        except Exception as e2:
            log.warning("   ⚠️  Debug capture échoué: %s", e2)
        return 1

    finally:
        rc.close()


if __name__ == "__main__":
    sys.exit(main())
