"""Probe complet des modèles LLM via bridge.probe() (parallèle, timeout).

Utilise la méthode `probe()` du bridge (DirectBridge) :
  - probe()                   → tous les modèles de tous les providers
  - probe(provider)           → tous les modèles du provider
  - probe(provider, model)    → un seul modèle
Chaque probe a un timeout réseau individuel ; les probes sont lancés en
threads simultanés (≤ 20) — un probe par fournisseur peut être lancé en
parallèle avec d'autres.

Les modèles en échec API (auth/404/timeout/credit) sont marqués
`unavailable` ; ceux qui répondent (même sans tool call) restent dispo.

Usage :
  python3 scripts/probe_all_models.py                     # tous providers
  python3 scripts/probe_all_models.py --providers kilo,nvidia
  python3 scripts/probe_all_models.py --provider opencode-zen --timeout 15
"""

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="", help="liste comma de providers "
                    "(ex: kilo,nvidia,google)")
    ap.add_argument("--provider", default="", help="un seul provider")
    ap.add_argument("--timeout", type=float, default=12.0,
                    help="timeout réseau PAR probe (s)")
    ap.add_argument("--dry-run", action="store_true",
                    help="ne modifie pas la BDD (affiche le tri seulement)")
    args = ap.parse_args()

    from services.api._shared import _get_cat
    from modules.llm_manager.llm_manager import LLMManager

    cat = _get_cat()
    bridge = LLMManager(cat).get_bridge()

    provs = [p.strip() for p in args.providers.split(",") if p.strip()]
    if args.provider:
        provs.append(args.provider.strip())
    provs = list(dict.fromkeys(provs))  # déduplique

    if args.dry_run:
        # Affiche le tri des modèles par score décroissant sans prober.
        cands = bridge._probe_candidates(provs[0] if len(provs) == 1 else None)
        print(f"{'provider':16} {'modèle':48} (ordre décroissant de score)")
        for c in cands[:60]:
            print(f"  {c['prov']:14} {c['pname'][:46]}")
        return 0

    t0 = time.time()
    if len(provs) == 1:
        print(f"Probe {provs[0]} (timeout {args.timeout}s/probe, threads ≤ 20)")
        res = bridge.probe(provider_ref=provs[0], timeout=args.timeout)
    else:
        print(f"Probe tous ({timeout} {args.timeout}s/probe, threads ≤ 20)")
        res = bridge.probe(timeout=args.timeout)

    dt = time.time() - t0
    print(f"\n=== RÉSULTAT ({dt:.0f}s) ===")
    print(f"  Probed       : {res['probed']}")
    print(f"  OK agentic   : {res['ok_agentic']}")
    print(f"  Non-agentic  : {res['non_agentic']}")
    print(f"  Unavailable  : {res['unavailable']}")

    # Détail compact : erreurs + modèles non-agentic.
    fails = [r for r in res["results"] if not r["ok"]]
    for r in fails[:40]:
        tag = r.get("error_code", "non-agentic")
        print(f"  ✗ {r['provider']}/{r['model'][:40]:40} {tag:12} "
              f"cmd={r.get('command','')[:30]} ({r.get('error','')[:30]})")
    if len(fails) > 40:
        print(f"  … +{len(fails)-40} autres")
    return 0


if __name__ == "__main__":
    sys.exit(main())
