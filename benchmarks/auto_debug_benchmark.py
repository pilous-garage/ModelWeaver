#!/usr/bin/env python3
"""auto_debug_benchmark — orchestration de niveaux de validation du swarm.

5 niveaux ENCHAINES (base → low → medium → high → complete). Chaque niveau
tourne ses benchmarks dans l'ordre ; le script S'ARRETE au premier problème :
  - benchmark timeouté (aucun sample complété / status error),
  - score global = 0 (échec total),
  - résultat invalide (pas de score lisible).

Durées cibles / timeouts par niveau (le timeout est le plafond PAR BENCHMARK) :
  base     : ≤ 1 min  (timeout 2 min)
  low      : ≤ 2 min  (timeout 2 min)
  medium   : ≤ 5 min  (timeout 5 min)
  high     : ≤ 10 min (timeout 10 min)
  complete : ≤ 30 min (timeout 30 min)

Usage :
  python3 benchmarks/auto_debug_benchmark.py [--agent swarm|proxy]
                                         [--from base] [--only base]
                                         [--workspace mw-llm-code]

Le but : valider que le swarm tient bout-en-bout AVANT de toucher au code
(migration). On ne bouge pas tant que `high` ne passe pas complètement.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))


# ── Définition des niveaux ────────────────────────────────────────────────
# Chaque niveau = liste de benchmarks (type_test, n, parallel, timeout_sample).
# Les timeouts de niveau sont des plafonds globaux par benchmark.
LEVELS: Dict[str, Dict[str, Any]] = {
    "base": {
        "max_min": 1, "timeout": 120,
        "benchmarks": [
            ("humaneval", 1, 1, 120),
        ],
    },
    "low": {
        "max_min": 2, "timeout": 120,
        "benchmarks": [
            ("humaneval", 2, 1, 120),
            ("mbpp", 2, 1, 120),
        ],
    },
    "medium": {
        "max_min": 5, "timeout": 300,
        "benchmarks": [
            ("gsm8k", 3, 1, 300),
            ("mmlu", 3, 1, 300),
            ("boolq", 3, 1, 300),
            ("hellaswag", 3, 1, 300),
        ],
    },
    "high": {
        "max_min": 10, "timeout": 600,
        "benchmarks": [
            ("humaneval", 5, 1, 600),
            ("mbpp", 5, 1, 600),
            ("gsm8k", 5, 1, 600),
            ("drop", 2, 1, 600),
        ],
    },
    "complete": {
        "max_min": 30, "timeout": 1800,
        "benchmarks": [
            ("humaneval", 5, 1, 600),
            ("mbpp", 5, 1, 600),
            ("gsm8k", 5, 1, 600),
            ("mmlu", 5, 1, 600),
            ("boolq", 5, 1, 600),
            ("hellaswag", 5, 1, 600),
            ("truthfulqa", 3, 1, 600),
            ("winogrande", 3, 1, 600),
        ],
    },
}
LEVEL_ORDER = ["base", "low", "medium", "high", "complete"]


def _score_ok(report: Dict[str, Any]) -> Tuple[bool, str]:
    """Retourne (ok, raison). ok=False → on arrête le niveau."""
    if not report.get("ok"):
        return False, f"erreur run: {report.get('error', '?')}"
    score = report.get("score", {})
    status = score.get("status", "")
    if status == "error":
        return False, "status=error (timeout / pas de sample complété)"
    # score global : on cherche une métrique de type 'accuracy'/'score'/'pass'
    metrics = score.get("metrics", {})
    if not metrics:
        return False, "aucun score lisible (métriques vides)"
    # détection d'un score global numérique
    best = None
    for k, v in metrics.items():
        if isinstance(v, (int, float)) and ("acc" in k.lower()
                                           or "score" in k.lower()
                                           or "pass" in k.lower()
                                           or k.endswith(".value")):
            best = v
            break
    if best is None:
        # pas de métrique numérique claire → on accepte tant que pas error
        return True, ""
    if best <= 0:
        return False, f"score global = {best} (échec total)"
    return True, ""


def _run_one(agent: str, b: Tuple[str, int, int, int], out_dir: Path) -> Dict[str, Any]:
    bench, n, parallel, timeout = b
    from benchmarks.run_benchmark_on import run_benchmark_on
    t0 = time.monotonic()
    try:
        r = run_benchmark_on(agent, bench, timeout=timeout,
                             parallel=parallel, n=n)
    except Exception as e:
        return {"ok": False, "error": f"exception: {e}"}
    r["_duration_s"] = round(time.monotonic() - t0, 1)
    # journalisation du rapport par benchmark
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{bench}_n{n}.json").write_text(
            json.dumps(r, indent=2, default=str))
    except Exception:
        pass
    return r


def _functional_swarm(workspace: str, timeout: int) -> Dict[str, Any]:
    """Bench fonctionnel swarm (pick → supervised) via bench_swarm_live."""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "benchmarks/bench_swarm_live.py",
             "--timeout", str(timeout), "--workspace", workspace],
            capture_output=True, text=True, timeout=timeout + 60)
        out = r.stdout + r.stderr
        ok = "status=ok" in out
        return {"ok": True, "functional_ok": ok,
                "tail": out[-400:]}
    except Exception as e:
        return {"ok": False, "error": f"functional: {e}"}


def _functional_swarm(workspace: str, timeout: int) -> Dict[str, Any]:
    """Bench fonctionnel swarm (pick → supervised) via bench_swarm_live."""
    import subprocess
    try:
        r = subprocess.run(
            [sys.executable, "benchmarks/bench_swarm_live.py",
             "--timeout", str(timeout), "--workspace", workspace],
            capture_output=True, text=True, timeout=timeout + 60)
        out = r.stdout + r.stderr
        ok = "status=ok" in out
        return {"ok": True, "functional_ok": ok,
                "tail": out[-400:]}
    except Exception as e:
        return {"ok": False, "error": f"functional: {e}"}


def run_level(level: str, agent: str, workspace: str, out_dir: Path) -> Dict[str, Any]:
    spec = LEVELS[level]
    print(f"\n{'='*70}\n NIVEAU {level.upper()} "
          f"(max {spec['max_min']}min, timeout {spec['timeout']}s/bench)\n"
          f"{'='*70}", flush=True)
    t_level = time.monotonic()
    results: List[Dict[str, Any]] = []

    for b in spec["benchmarks"]:
        bench, n, parallel, timeout = b
        print(f"\n→ [{agent}] {bench} x{n} (parallel={parallel}, "
              f"timeout={timeout}s)", flush=True)
        r = _run_one(agent, b, out_dir / level)
        ok, raison = _score_ok(r)
        results.append({"kind": "bench", "bench": bench, "n": n,
                        "ok": ok, "raison": raison, "report": r})
        if not ok:
            return {"level": level, "ok": False,
                    "reason": f"{bench} x{n}: {raison}", "results": results}
        print(f"  ✓ {bench} x{n} OK ({r.get('_duration_s')}s)", flush=True)

    dur = round(time.monotonic() - t_level, 1)
    return {"level": level, "ok": True, "duration_s": dur, "results": results}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--agent", default="swarm", choices=["swarm", "proxy"])
    ap.add_argument("--workspace", default="mw-llm-code")
    ap.add_argument("--from", dest="from_level", default="base",
                    choices=LEVEL_ORDER)
    ap.add_argument("--only", default="", choices=LEVEL_ORDER)
    ap.add_argument("--out", default="logs/auto_debug_benchmark")
    args = ap.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    levels = [args.only] if args.only else (
        LEVEL_ORDER[LEVEL_ORDER.index(args.from_level):])

    global_report: List[Dict[str, Any]] = []
    for level in levels:
        t0 = time.monotonic()
        rep = run_level(level, args.agent, args.workspace, out_dir)
        rep["wall_s"] = round(time.monotonic() - t0, 1)
        global_report.append(rep)
        # rapport intermédiaire
        summary = {
            "level": level,
            "ok": rep["ok"],
            "reason": rep.get("reason", ""),
            "wall_s": rep["wall_s"],
        }
        print(f"\n>>> NIVEAU {level}: {'OK' if rep['ok'] else 'ÉCHEC'} "
              f"({rep['wall_s']}s)"
              + (f" — {rep.get('reason','')}" if not rep['ok'] else ""),
              flush=True)
        # sauvegarde incrémentale
        (out_dir / "report.json").write_text(
            json.dumps(global_report, indent=2, default=str))
        if not rep["ok"]:
            print(f"\nARRÊT sur échec du niveau {level}.", flush=True)
            break

    final = {
        "ok": all(r["ok"] for r in global_report),
        "levels": global_report,
        "stopped_at": (levels[-1] if global_report else None),
    }
    print("\n" + "="*70)
    print("RÉSUMÉ AUTO_DEBUG_BENCHMARK:")
    for r in global_report:
        print(f"  {r['level']:>8}: {'OK ' if r['ok'] else 'FAIL'} "
              f"({r.get('wall_s')}s)"
              + (f" — {r.get('reason','')}" if not r['ok'] else ""))
    print(f"GLOBAL: {'OK' if final['ok'] else 'ÉCHEC'}")
    print("="*70)
    (out_dir / "report.json").write_text(
        json.dumps(global_report, indent=2, default=str))


if __name__ == "__main__":
    main()
