#!/usr/bin/env python3
"""Exporte le graphe déplié d'un agent en YAML via l'API du daemon.
Parcourt récursivement tous les steps (while body, for body, if branches, etc.)
et résout chaque call skill.

Usage:
    python3 tools/export_graph_yaml.py [--agent worker] [--port 8770]

Sauvegarde dans /tmp/{agent}_graph_expanded.yaml
"""

import os, sys, json, time, argparse, logging
from pathlib import Path
from typing import Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("export_graph")

ROOT = Path(__file__).resolve().parent.parent


# ── Token / API ──

def _read_token() -> str:
    token_path = Path.home() / ".modelweaver" / "api.token"
    if token_path.exists():
        return token_path.read_text().strip()
    return ""

def daemon_post(route: str, body: dict = None, port: int = 8770) -> dict:
    import urllib.request, urllib.error
    url = f"http://127.0.0.1:{port}/v1/{route}"
    data = json.dumps(body or {}).encode()
    headers = {"Content-Type": "application/json"}
    token = _read_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"

    for attempt in range(5):
        req = urllib.request.Request(url, data=data, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_err = e.read().decode()[:200]
            if e.code == 429:
                retry_after = 15 * (attempt + 1)
                log.warning("   ⏳ Rate limit (%s), attente %ds (tentative %d/5)...", route, retry_after, attempt + 1)
                time.sleep(retry_after)
                continue
            log.error("   HTTP %d: %s", e.code, body_err)
            raise
        except Exception as e:
            log.error("   Erreur requête %s: %s", url, e)
            if attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            raise
    raise RuntimeError(f"Abandon après 5 tentatives: {route}")


# ── Extraction des steps depuis un doc YAML ──

def extract_steps(doc: dict) -> tuple:
    """Extrait les steps d'un document (entrypoints > workflow.steps > steps).
    Renvoie (steps, entrypoint_name).
    """
    eps = doc.get("entrypoints", {})
    if eps:
        ep_names = list(eps.keys())
        main_ep = eps.get("main") or eps.get(ep_names[0])
        ep_name = "main" if "main" in eps else ep_names[0]
        steps = main_ep.get("steps", [])
        if steps:
            return steps, ep_name
    wf = doc.get("workflow", {})
    if wf:
        steps = wf.get("steps", [])
        if steps:
            return steps, "workflow"
    if "steps" in doc:
        return doc["steps"], "root"
    return [], ""


# ── Parcours récursif de tous les steps (y compris corps imbriqués) ──

def walk_steps(steps: list, port: int, max_depth: int, depth: int = 0,
               visited: set = None) -> list:
    """Parcourt récursivement une liste de steps, en résolvant les call skills
    et en descendant dans les corps (while/for/if/switch)."""
    if visited is None:
        visited = set()
    resolved = []
    for st in steps:
        if not isinstance(st, dict):
            resolved.append(st)
            continue

        st = dict(st)  # copie pour ne pas muter l'original

        # Résoudre les appels de skills
        if st.get("type") == "call" and st.get("fn"):
            fn = st["fn"]
            if fn not in visited and depth < max_depth:
                log.info("   📥 Fetch skill: %s (depth=%d)", fn, depth)
                skill_doc = fetch_skill_doc(fn, port)
                if skill_doc:
                    inner, _ = extract_steps(skill_doc)
                    if inner:
                        visited.add(fn)
                        expanded = walk_steps(inner, port, max_depth, depth + 1, visited)
                        st["_resolved_skill"] = {"name": fn, "steps": expanded, "depth": depth}
                        log.info("     → %d steps dépliés dans %s", len(expanded), fn)

        # Descendre dans les corps de boucles/conditions
        for body_key in ("body", "body_true", "body_false"):
            body = st.get(body_key)
            if isinstance(body, dict):
                body_steps = body.get("steps", [])
                if body_steps:
                    body["steps"] = walk_steps(body_steps, port, max_depth, depth, visited)
            elif isinstance(body, list):
                st[body_key] = walk_steps(body, port, max_depth, depth, visited)

        # Switch conditions
        conditions = st.get("conditions", [])
        if isinstance(conditions, list):
            for cond in conditions:
                if isinstance(cond, dict):
                    cnext = cond.get("next")
                    # Les conditions n'ont pas de body, juste un next

        resolved.append(st)

    return resolved


# ── Cache skills ──

_skill_cache: dict = {}
_skill_fetching: set = set()

def fetch_skill_doc(fn: str, port: int) -> Optional[dict]:
    """Récupère le YAML d'un skill (avec cache et rate limiting)."""
    if fn in _skill_cache:
        return _skill_cache[fn]

    if fn in _skill_fetching:
        return None  # déjà en cours
    _skill_fetching.add(fn)

    for attempt in range(5):
        try:
            res = daemon_post("catalogue/skills/get", {"name": fn}, port=port)
        except Exception as e:
            err_str = str(e)
            if "429" in err_str or "rate_limited" in err_str:
                retry_after = 15 * (attempt + 1)
                log.warning("   ⏳ Rate limit, attente %ds...", retry_after)
                time.sleep(retry_after)
                continue
            log.warning("   ⚠️  Fetch échoué %s: %s", fn, e)
            _skill_fetching.discard(fn)
            return None

        yaml_str = res.get("result", {}).get("yaml") or res.get("yaml", "")
        if not yaml_str:
            log.warning("   ⚠️  Pas de YAML pour %s", fn)
            _skill_fetching.discard(fn)
            return None

        import yaml
        doc = yaml.safe_load(yaml_str) or {}
        _skill_cache[fn] = doc
        _skill_fetching.discard(fn)
        return doc

    _skill_fetching.discard(fn)
    log.warning("   ⚠️  Fetch abandonné %s après 5 tentatives", fn)
    return None


# ── Comptage récursif ──

def count_resolved(steps: list) -> int:
    """Compte le nombre total de steps après résolution (récursif)."""
    total = len(steps)
    for st in steps:
        if isinstance(st, dict):
            rs = st.get("_resolved_skill", {})
            if rs:
                total += count_resolved(rs.get("steps", []))
            for body_key in ("body", "body_true", "body_false"):
                body = st.get(body_key)
                if isinstance(body, dict):
                    total += count_resolved(body.get("steps", []))
                elif isinstance(body, list):
                    total += count_resolved(body)
    return total


# ── Construction du graphe expansé ──

def build_expanded_graph(agent_name: str, port: int, max_depth: int = 5) -> dict:
    """Construit le graphe complet d'un agent avec tous les skills dépliés."""
    log.info("=" * 60)
    log.info("📊 Export graphe expansé: agent=%s, port=%d, max_depth=%d", agent_name, port, max_depth)
    log.info("=" * 60)

    # 1. Fetch agent
    log.info("📥 Fetch agent '%s'...", agent_name)
    res = daemon_post("catalogue/agents/get", {"name": agent_name}, port=port)
    yaml_str = res.get("result", {}).get("yaml") or res.get("yaml", "")
    if not yaml_str:
        log.error("❌ Agent '%s' non trouvé", agent_name)
        sys.exit(1)

    import yaml
    agent_doc = yaml.safe_load(yaml_str) or {}

    # 2. Extraire les steps
    base_steps, ep_name = extract_steps(agent_doc)
    log.info("   %d steps à la racine de l'agent (entrypoint: %s)", len(base_steps), ep_name)

    # 3. Parcourir et résoudre récursivement
    resolved_steps = walk_steps(base_steps, port, max_depth, 1)

    # 4. Compter
    total = count_resolved(resolved_steps)
    log.info("   📊 Total steps (déplié): %d", total)

    # 5. Compter les skills résolus
    def count_skills(s):
        n = 0
        for st in s:
            if isinstance(st, dict):
                if "_resolved_skill" in st:
                    n += 1
                for bk in ("body", "body_true", "body_false"):
                    b = st.get(bk)
                    if isinstance(b, dict):
                        n += count_skills(b.get("steps", []))
                    elif isinstance(b, list):
                        n += count_skills(b)
        return n
    skills_done = count_skills(resolved_steps)

    # 6. Construire le graphe final
    eps = agent_doc.get("entrypoints", {})
    graph = {
        "_meta": {
            "agent": agent_name,
            "port": port,
            "max_depth": max_depth,
            "total_steps_base": len(base_steps),
            "total_steps_expanded": total,
            "skills_resolved": skills_done,
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
        "agent": agent_doc.get("name", agent_name),
        "role": agent_doc.get("role", ""),
        "personality": agent_doc.get("personality", {}),
        "skills": agent_doc.get("skills", []),
        "entrypoints": {
            name: {"steps": resolved_steps if name == ep_name else ep.get("steps", [])}
            for name, ep in eps.items()
        } if eps else {},
        "workflow": {
            "steps": resolved_steps
        } if not eps else {},
    }

    return graph


# ── Main ──

def main():
    parser = argparse.ArgumentParser(description="Export graphe expansé d'un agent en YAML")
    parser.add_argument("--agent", default="worker", help="Nom de l'agent (défaut: worker)")
    parser.add_argument("--port", type=int, default=8770, help="Port du daemon (défaut: 8770)")
    parser.add_argument("--output", default="", help="Fichier de sortie (défaut: /tmp/{agent}_graph_expanded.yaml)")
    parser.add_argument("--max-depth", type=int, default=5, help="Profondeur max de résolution (défaut: 5)")
    parser.add_argument("--wait", type=int, default=0, help="Attendre N secondes avant de commencer")
    args = parser.parse_args()

    if args.wait:
        log.info("⏳ Attente %ds pour chargement BDD...", args.wait)
        time.sleep(args.wait)

    output = args.output or f"/tmp/{args.agent}_graph_expanded.yaml"

    graph = build_expanded_graph(args.agent, args.port, args.max_depth)

    import yaml
    yaml_str = yaml.dump(graph, default_flow_style=False, allow_unicode=True,
                         sort_keys=False, line_break=True)

    Path(output).write_text(yaml_str, encoding="utf-8")
    meta = graph["_meta"]
    log.info("✅ Graphe expansé sauvegardé → %s (%d chars, %d lignes)",
             output, len(yaml_str), yaml_str.count("\n"))
    log.info("📊 Stats: %d steps (base) → %d steps (expansé), %d skills résolus",
             meta["total_steps_base"], meta["total_steps_expanded"], meta["skills_resolved"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
