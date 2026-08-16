#!/usr/bin/env python3
"""run_benchmark_on — orchestration des benchmarks (proxy ou swarm).

Deux fonctions :
  - run_benchmark_on(agent_entry, type_test, timeout, parallel=1, n=10)
      exécute UN type de test sur UN agent entry.
  - run_all_benchmark_on(agent_entry, timeout, parallel=1, n=10)
      exécute TOUS les types de test (suite par défaut) séquentiellement.

agent_entry : "proxy" | "swarm"
type_test   : "humaneval" | "mbpp" | "gsm8k" | ... (tout module inspect_evals)

Stratégie (recommandée) :
  1. Tests INDÉPENDANTS sur le proxy : parallel=1, timeout=180 (3 min) par
     type, un type après l'autre → vérifier des résultats COHÉRENTS (pas
     d'erreurs de typage de réponse).
  2. Multi-requêtes : parallel=10, timeout=180 → le proxy doit gérer 10
     questions simultanées.
  3. Ensuite seulement : passer sur swarm (agent_entry="swarm").

Chaque éval génère un log inspect_ai .eval dans logs/ ; le rapport est lu
depuis log.results.scores[].metrics (format inspect actuel).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

# Suite de tests par défaut (types cohérents avec le proxy : réponse texte
# simple, pas de sandbox lourd).
DEFAULT_TESTS = ["humaneval", "mbpp"]

# Tous les types inspect_evals disponibles (après filtrage des lourds/sandbox).
HEAVY_TESTS = {"swe_bench", "swe_lancer", "cybergym", "browse_comp",
               "assistant_bench", "theagentcompany", "agentdojo",
               "agentharm", "cybermetric", "cyberseceval_2", "cyberseceval_3",
               "cyberseceval_4", "agent_bench", "compute_eval", "scicode",
               "vimgolf_challenges", "agentic_misalignment", "pre_flight"}


def _api_token() -> str:
    mw = Path.home() / ".modelweaver"
    tf = mw / "api.token"
    return tf.read_text().strip() if tf.exists() else ""


def _read_score(eval_log_path: Path) -> Dict[str, Any]:
    """Lit le score depuis un log inspect .eval (format actuel)."""
    from inspect_ai.log import read_eval_log
    log = read_eval_log(str(eval_log_path))
    out: Dict[str, Any] = {"status": log.status, "metrics": {}}
    scores = getattr(getattr(log, "results", None), "scores", None)
    if scores:
        for s in scores:
            name = getattr(s, "name", "verify")
            metrics = getattr(s, "metrics", {}) or {}
            for mname, m in metrics.items():
                val = m.get("value") if isinstance(m, dict) else getattr(m, "value", "?")
                out["metrics"][f"{name}.{mname}"] = val
            out["scored"] = getattr(s, "scored_samples", 0)
            out["unscored"] = getattr(s, "unscored_samples", 0)
    return out


def run_benchmark_on(agent_entry: str, type_test: str,
                     timeout: int = 180, parallel: int = 1, n: int = 10,
                     shuffle_seed: int = 0) -> Dict[str, Any]:
    """Exécute un type de test sur un agent entry.

    Args:
        agent_entry: "proxy" | "swarm"
        type_test: nom d'un benchmark inspect_evals (humaneval, mbpp...)
        timeout: timeout par sample en secondes (défaut 180 = 3 min)
        parallel: nb de requêtes simultanées (1 = séquentiel ; 10 = burst)
        n: nb de problèmes (limit)
        shuffle_seed: seed pour mélanger avant de prendre les N (0 = ordre)
    """
    if agent_entry not in ("proxy", "swarm"):
        return {"ok": False, "error": f"agent_entry inconnu: {agent_entry} "
                                      f"(proxy | swarm)"}
    t_start = time.monotonic()
    from benchmarks.run_inspect_swarm import (
        _build_task, _print_report,
    )
    from inspect_ai import eval as inspect_eval
    from inspect_ai.model import get_model

    base_url = "http://127.0.0.1:8770/v1"
    token = _api_token()
    if not token:
        return {"ok": False, "error": "token API introuvable — daemon up ?"}
    model_name = "mw-proxy" if agent_entry == "proxy" else "mw-swarm-build"
    model = get_model(f"openai/{model_name}", base_url=base_url, api_key=token)

    task = _build_task(type_test, n)
    print(f"→ [{agent_entry}] {type_test} x{n} (parallel={parallel}, "
          f"timeout={timeout}s, seed={shuffle_seed or 'ordre'})", flush=True)
    try:
        result = inspect_eval(task, model=model, limit=n,
                              sample_shuffle=shuffle_seed or None,
                              sandbox="local",
                              max_samples=parallel,
                              time_limit=timeout)
    except Exception as e:
        return {"ok": False, "error": f"inspect_eval: {e}"}
    _print_report(result)
    # Récupérer le dernier log .eval généré pour ce run.
    eval_log = None
    try:
        import glob
        cands = sorted(Path("logs").glob(f"*{type_test}*.eval"))
        if cands:
            eval_log = str(cands[-1])
    except Exception:
        pass
    score = _read_score(eval_log) if eval_log else {}
    report = {
        "ok": True,
        "agent_entry": agent_entry, "type_test": type_test, "n": n,
        "parallel": parallel, "timeout": timeout,
        "duration_s": round(time.monotonic() - t_start, 1),
        "eval_log": eval_log,
        "score": score,
    }
    return report


def run_all_benchmark_on(agent_entry: str, timeout: int = 180,
                         parallel: int = 1, n: int = 10,
                         tests: Optional[List[str]] = None) -> Dict[str, Any]:
    """Exécute TOUS les types de test séquentiellement sur un agent entry.

    Chaque type est exécuté indépendamment (parallel/séparé) et ses résultats
    sont collectés. Si un type échoue en erreur de typage/format, il est noté
    mais on continue les autres (pour voir lesquels sont cohérents).
    """
    tests = tests or DEFAULT_TESTS
    results: List[Dict[str, Any]] = []
    t_start = time.monotonic()
    for t in tests:
        print(f"\n{'='*60}\nTEST {t} sur {agent_entry}\n{'='*60}", flush=True)
        try:
            r = run_benchmark_on(agent_entry, t, timeout=timeout,
                                 parallel=parallel, n=n)
        except Exception as e:
            r = {"ok": False, "type_test": t, "error": str(e)}
        results.append(r)
        # pause courte entre les types (rate-limit des providers)
        time.sleep(2)
    return {"ok": True, "agent_entry": agent_entry, "tests": results,
            "duration_s": round(time.monotonic() - t_start, 1)}


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("agent_entry", choices=["proxy", "swarm"])
    parser.add_argument("--test", default="",
                        help="un type de test (humaneval, mbpp...) — vide = tous")
    parser.add_argument("--timeout", type=int, default=180)
    parser.add_argument("--parallel", type=int, default=1)
    parser.add_argument("--n", type=int, default=10)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--out", default="",
                        help="fichier JSON de sortie (rapport)")
    args = parser.parse_args()

    if args.test:
        report = run_benchmark_on(args.agent_entry, args.test,
                                  timeout=args.timeout, parallel=args.parallel,
                                  n=args.n, shuffle_seed=args.seed)
        print(json.dumps(report, indent=2, default=str))
    else:
        report = run_all_benchmark_on(args.agent_entry, timeout=args.timeout,
                                      parallel=args.parallel, n=args.n)
        print(json.dumps(report, indent=2, default=str))
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2, default=str))


if __name__ == "__main__":
    main()
