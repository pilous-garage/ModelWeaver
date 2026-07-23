#!/usr/bin/env python3
"""
Smoke API — test sans navigateur via le daemon.
Suppose que `npm run tauri dev` tourne déjà (daemon sur 8771, frontend sur 5173).

Séquence :
  1. Vérifie daemon + récupère le token
  2. Charge l'agent worker depuis le catalogue
  3. Extrait les steps du workflow → nodes + edges
  4. Sauvegarde /tmp/smoke_worker.yaml + /tmp/smoke_graph.yaml + /tmp/smoke_graph.json

Usage :
    python3 tests/smoke_api.py
    python3 tests/smoke_api.py --port 8771
"""

import argparse
import json
import sys
import time
from pathlib import Path

try:
    import requests
    import yaml
except ImportError as e:
    print(f"❌ Dépendance manquante : {e}\n   pip install requests pyyaml")
    sys.exit(1)

REPO = Path(__file__).resolve().parent.parent
CATALOGUE = REPO / "AgentsCatalogue"


def find_token_and_port():
    mw_dir = REPO / ".modelweaver"
    for name in ("api.token", "token"):
        p = mw_dir / name
        if p.exists():
            tok = p.read_text(encoding="utf-8").strip()
            if tok:
                return tok, 8771
    return None, 8771


def wait_daemon(base, timeout=30):
    t0 = time.time()
    last = None
    while time.time() - t0 < timeout:
        try:
            r = requests.get(f"{base}/health", timeout=2)
            if r.ok:
                return True
            last = r.status_code
        except Exception as e:
            last = repr(e)
        time.sleep(0.5)
    print(f"[smoke] daemon indisponible ({last})")
    return False


def api(base, route, payload=None, token=None):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    full = f"v1/{route}" if not route.startswith("v1/") else route
    r = requests.post(f"{base}/{full}", json=payload or {}, headers=headers, timeout=20)
    r.raise_for_status()
    data = r.json()
    if not data.get("ok", False):
        raise RuntimeError(f"API KO {full}: {data}")
    return data.get("result", data)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8771)
    ap.add_argument("--timeout", type=int, default=60)
    args = ap.parse_args()

    base = f"http://127.0.0.1:{args.port}"

    # ── 1. Daemon ──
    print(f"[smoke] attente daemon {base}")
    if not wait_daemon(base, timeout=args.timeout):
        return 1

    token, _ = find_token_and_port()
    if not token:
        print("[smoke] ⚠️ aucun token, tentative sans auth")
    else:
        print(f"[smoke] token OK ({token[:8]}...)")

    # ── 2. Charger worker ──
    print("[smoke] chargement agent worker...")
    try:
        agent_data = api(base, "catalogue/agents/get", {"name": "worker"}, token)
        yaml_text = agent_data.get("yaml", "") or agent_data.get("inline_yaml", "")
    except Exception as e:
        print(f"[smoke] ❌ échec chargement worker : {e}")
        return 1

    if not yaml_text:
        print("[smoke] ❌ worker YAML vide")
        return 1

    Path("/tmp/smoke_worker.yaml").write_text(yaml_text, encoding="utf-8")
    print(f"[smoke] worker YAML → /tmp/smoke_worker.yaml ({len(yaml_text)} chars)")

    obj = yaml.safe_load(yaml_text) or {}

    # ── 3. Extraire steps → nodes/edges ──
    entrypoints = obj.get("entrypoints") or {}
    ep_name = next(iter(entrypoints)) if entrypoints else "main"
    steps = ((entrypoints.get(ep_name) or {}).get("steps", []) or
             obj.get("workflow", {}).get("steps", []))

    nodes, edges = [], []
    for idx, step in enumerate(steps):
        sid = step.get("id", f"step_{idx}")
        stype = step.get("type", "unknown")
        sfn = step.get("fn", "")
        nxt = step.get("next")
        label = f"{sid}\n{stype}" + (f"\n{sfn}" if sfn else "")
        nodes.append({"id": sid, "label": label, "type": stype})
        if nxt:
            edges.append({"from": sid, "to": nxt})

    Path("/tmp/smoke_graph.json").write_text(
        json.dumps({"nodes": nodes, "edges": edges}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"[smoke] 📊 nœuds={len(nodes)}, arêtes={len(edges)} → /tmp/smoke_graph.json")

    # ── 4. .graph.agent.yaml minimal ──
    graph_obj = {
        "name": obj.get("name", "worker"),
        "role": obj.get("role", ""),
        "graph_entrypoint": ep_name,
        "nodes": [
            {
                "id": n["id"],
                "type": n["type"],
                "fn": next((s.get("fn") for s in steps if s.get("id") == n["id"]), ""),
            }
            for n in nodes
        ],
        "edges": edges,
    }
    graph_yaml = yaml.dump(graph_obj, default_flow_style=False, allow_unicode=True, sort_keys=False)
    Path("/tmp/smoke_graph.yaml").write_text(graph_yaml, encoding="utf-8")
    print(f"[smoke] 📄 .graph.agent.yaml → /tmp/smoke_graph.yaml ({len(graph_yaml)} chars)")

    print("[smoke] ✅ OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())