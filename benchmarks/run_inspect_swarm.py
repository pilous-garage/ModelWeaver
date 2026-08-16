#!/usr/bin/env python3
"""run_inspect_swarm — Lance un benchmark Inspect contre le swarm (endpoint
OpenAI-compatible /v1/chat/completions).

Usage :
    python3 benchmarks/run_inspect_swarm.py [--port 8770] [--n 5] [--bench humaneval]
    python3 benchmarks/run_inspect_swarm.py --bench mbpp --n 10
    python3 benchmarks/run_inspect_swarm.py --bench factorial --n 3

Prérequis :
  - le daemon ModelWeaver tourne (services/api/daemon.py, port par défaut 8770)
  - inspect_ai installé dans .venv-bench :
        uv pip install --python .venv-bench/bin/python \
            -r benchmarks/requirements.txt
  - inspect_evals pour les vrais benchmarks (HumanEval, MBPP…) :
        uv pip install --python .venv-bench/bin/python inspect-evals

Le swarm est vu comme un « modèle » OpenAI (base_url → daemon). Le mode
(plan/build) est piloté par le nom du modèle : "mw-swarm-build" → build.

`--bench` :
  - humaneval (défaut) : 164 problèmes de code Python (openai_humaneval, HF).
  - mbpp             : MBPP (Mostly Basic Programming Problems).
  - factorial        : mini-dataset de démo trivial (pas de HF).
"""

import argparse
import os
from pathlib import Path

import inspect_ai  # noqa: F401  (vérifie l'install)

REPO = Path(__file__).resolve().parent.parent


def _api_token(port: int) -> str:
    """Le token du daemon (~/.modelweaver/api.token)."""
    mw = Path(os.environ.get("MODELWEAVER_HOME") or
              os.environ.get("MW_HOME") or Path.home() / ".modelweaver")
    tf = mw / "api.token"
    return tf.read_text().strip() if tf.exists() else ""


def _build_task(bench: str, n: int):
    """Construit la tâche Inspect selon le benchmark demandé."""
    from inspect_ai import Task
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.solver import generate

    if bench == "factorial":
        samples = [Sample(
            input="Écris une fonction Python `fact(n)` qui calcule la factorielle "
                  "de n (récursive). Réponds avec le code uniquement.",
            target="120",
            id=f"fact-{i}",
        ) for i in range(n)]
        return Task(dataset=MemoryDataset(samples), solver=[generate()])

    if bench in ("humaneval", "mbpp"):
        from inspect_ai.solver import generate
        if bench == "humaneval":
            from inspect_evals.humaneval import humaneval
            task = humaneval()
        else:
            from inspect_evals.mbpp import mbpp
            task = mbpp()
        # Borner au nombre demandé via le solver génération (limit au eval).
        return task

    raise ValueError(f"benchmark inconnu: {bench} "
                     f"(disponibles: humaneval, mbpp, factorial)")


def main() -> None:
    import sys
    sys.path.insert(0, str(REPO))
    from inspect_ai import eval as inspect_eval
    from inspect_ai.model import get_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--n", type=int, default=5)
    parser.add_argument("--bench", default="humaneval",
                        choices=["humaneval", "mbpp", "factorial"])
    parser.add_argument("--sandbox", default="local",
                        help="sandbox inspect_ai (local | docker ; défaut local — "
                             "pas de plugin docker compose requis)")
    parser.add_argument("--max-parallel", type=int, default=20,
                        help="samples exécutés en parallèle par Inspect (défaut "
                             "20 — le swarm a 8 codeurs + max_concurrent 20)")
    parser.add_argument("--time-limit", type=int, default=5400,
                        help="limite de temps par sample en secondes (défaut "
                             "5400 = 90 min — le swarm met 5-30 min par sample)")
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}/v1"
    token = _api_token(args.port)
    if not token:
        print("⚠️  token API introuvable (~/.modelweaver/api.token) — le daemon "
              "tourne-t-il ?")
        return

    print(f"→ swarm branché sur {base_url} (modèle mw-swarm-build)")
    # Pas de model_args : le SDK openai 2.x injecté par inspect_ai 0.3.255
    # les transmet tels quels à AsyncOpenAI (bug de compatibilité) → TypeError.
    model = get_model("openai/mw-swarm-build",
                      base_url=base_url, api_key=token)

    task = _build_task(args.bench, args.n)
    # PHASE CANCELLATION préalable : annule les vieilles tâches du workspace
    # pour que le swarm ne traite QUE le benchmark (les tâches résiduelles
    # polluent le picker et détournent les greedy).
    _cancel_workspace("mw-llm-code",
                      reason="reset avant benchmark inspect (phase cancellation)")
    print(f"→ évaluation {args.bench} x{args.n} (sandbox={args.sandbox}, "
          f"max_samples={args.max_parallel}, time_limit={args.time_limit}s, "
          "peut être long : le swarm orchestre des agents réels)…")
    try:
        result = inspect_eval(task, model=model, limit=args.n,
                              sandbox=args.sandbox,
                              max_samples=args.max_parallel,
                              time_limit=args.time_limit)
    except KeyboardInterrupt:
        print("\n→ interruption — phase cancellation…", flush=True)
        _cancel_workspace("mw-llm-code",
                          reason="benchmark inspect interrompu")
        return
    for r in result:
        st = getattr(r, "status", "?")
        sc = getattr(getattr(r, "results", None), "score", None)
        acc = sc.accuracy if sc else float("nan")
        answered = sc.total_answered if sc else 0
        nm = getattr(getattr(r, "eval", None), "model", "?")
        print(f"\n[score] {nm} : {st} "
              f"| accuracy={acc:.2f} ({answered} réponses)")
    # PHASE CANCELLATION UNIQUEMENT si l'évaluation a échoué (statut non
    # completed) ou sur interruption. Si elle a réussi, on laisse les sessions
    # et le swarm finir de travailler au besoin — annuler casserait un run qui
    # progresse.
    failed = any(getattr(r, "status", "completed") != "completed"
                 for r in result)
    if failed:
        _cancel_workspace("mw-llm-code",
                          reason="benchmark inspect en échec (phase cancellation)")


def _cancel_workspace(workspace: str, reason: str = "") -> None:
    """PHASE CANCELLATION après le benchmark : annule proprement les tâches de
    session restantes (signal aux agents + flag cancelled + branche protégée)
    au lieu de les laisser en todo/doing polluer le prochain benchmark."""
    try:
        from services.benchmark_cancel import cancel_workspace
        r = cancel_workspace(workspace, repo_prefix="sessions/", reason=reason)
        if r.get("errors"):
            for e in r["errors"]:
                print(f"[cancel] tâche {e.get('task_id')}: {e.get('error')}",
                      flush=True)
        if r.get("count"):
            print(f"[cancel] {r['count']} tâches de session annulées "
                  f"({r.get('cleaned_repos', 0)} repos nettoyés)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[cancel] échec phase cancellation: {e}", flush=True)


if __name__ == "__main__":
    main()
