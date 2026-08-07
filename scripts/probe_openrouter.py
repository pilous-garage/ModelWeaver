"""Probe openrouter du meilleur au moins performant (50 req/jour TOTALES !).

L'utilisateur veut : trier les modèles openrouter par score (capacité),
puis probe du plus performant au moins performant jusqu'à ce qu'un réponde
(ils ne marquent pas « free » → c'est le meilleur moyen de trouver le plus
efficace utilisable). Consomme 1 req par probe → rester sous 50/jour.

Usage:
  python3 scripts/probe_openrouter.py [--max 10] [--sleep 3] [--only "gpt-5,claude"]
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Candidats openrouter supposés les plus performants, du meilleur au moins
# bon (ordre approximatif de capacité réelle). On s'arrête au 1er qui répond.
# Les noms sont ceux retournés par l'API openrouter (provider_model_name).
CANDIDATES = [
    "openrouter/anthropic/claude-sonnet-4.5",
    "openrouter/anthropic/claude-opus-4.1",
    "openrouter/openai/gpt-5.6",
    "openrouter/openai/gpt-5.1",
    "openrouter/openai/gpt-5.1-codex-max",
    "openrouter/google/gemini-3.6-pro",
    "openrouter/google/gemini-3.5-flash",
    "openrouter/deepseek/deepseek-v4",
    "openrouter/qwen/qwen3.5-coder",
    "openrouter/meta-llama/llama-4-scout",
    "openrouter/poolside/laguna-s-2.1:free",
]

PROBE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "probe_reply_v1",
        "description": "Répond au probe.",
        "parameters": {"type": "object",
                       "properties": {"ok": {"type": "boolean"}},
                       "required": ["ok"]},
    },
}]
PROBE_USER = "Appelle probe_reply_v1 avec ok=true. Réponds UNIQUEMENT via l'outil."


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=10)
    ap.add_argument("--sleep", type=int, default=3)
    ap.add_argument("--only", default="", help="filtre substring sur le nom")
    args = ap.parse_args()

    from modules.sql.db import CatalogueDB
    from modules.llm_manager.llm_manager import LLMManager
    from scripts.probe_providers import _log_call, _probe

    cat = CatalogueDB()
    llm = LLMManager(cat)
    bridge = llm.get_bridge()

    cands = CANDIDATES
    if args.only:
        cands = [c for c in cands if args.only in c]
    cands = cands[:args.max]

    print(f"Probe openrouter : {len(cands)} candidats (sleep={args.sleep}s)")
    for name in cands:
        # name = openrouter/xxx → provider_ref=openrouter, model_ref=xxx
        provider_ref, _, model_ref = name.partition("/")
        model_ref = name  # l'API veut le nom complet côté openrouter
        r = _probe(bridge, "openrouter", name)
        _log_call(cat, "openrouter", name, r["ok"], r.get("latency_ms", 0),
                  r.get("error_code", ""), r.get("error", ""))
        status = "OK ✓" if r["ok"] else ("ERR " + r.get("error_code", ""))
        print(f"  {name}: {status} ({r.get('latency_ms',0)}ms) "
              f"tools={r.get('tool_calls')}")
        if r["ok"]:
            print(f"\n  → PREMIER MODÈLE OPÉRATIONNEL : {name}")
            return 0
        time.sleep(args.sleep)

    print("\n  Aucun modèle openrouter opérationnel parmi les candidats.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
