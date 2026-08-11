#!/usr/bin/env python3
"""run_inspect_swarm — Lance un benchmark Inspect contre le swarm (endpoint
OpenAI-compatible /v1/chat/completions).

Usage :
    python3 benchmarks/run_inspect_swarm.py [--port 8770] [--n 5]

Prérequis :
  - le daemon ModelWeaver tourne (services/api/daemon.py, port par défaut 8770)
  - inspect_ai installé : uv pip install --python .venv-bench/bin/python \
        -r benchmarks/requirements.txt

Le swarm est vu comme un « modèle » OpenAI (base_url → daemon). Le mode
(plan/build) est piloté par le nom du modèle : "mw-swarm-build" → build.

Exemple de dataset simple : on demande une fonction factorielle et on vérifie
la sortie (assert type=python). Les vrais datasets (HumanEval, MBPP) se
branchent de la même façon — voir docs/swarm_as_llm.md.
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


def main() -> None:
    from inspect_ai import Task, eval as inspect_eval
    from inspect_ai.dataset import MemoryDataset, Sample
    from inspect_ai.model import get_model
    from inspect_ai.solver import generate

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--n", type=int, default=5)
    args = parser.parse_args()

    base_url = f"http://127.0.0.1:{args.port}/v1"
    token = _api_token(args.port)
    if not token:
        print("⚠️  token API introuvable (~/.modelweaver/api.token) — le daemon "
              "tourne-t-il ?")
        return

    print(f"→ swarm branché sur {base_url} (modèle mw-swarm-build)")
    model = get_model("openai/mw-swarm-build",
                      base_url=base_url, api_key=token)

    # Dataset de démo : demander une fonction factorielle, vérifier la sortie.
    samples = [Sample(
        input="Écris une fonction Python `fact(n)` qui calcule la factorielle "
              "de n (récursive). Réponds avec le code uniquement.",
        target="120",
        id=f"fact-{i}",
    ) for i in range(args.n)]

    task = Task(dataset=MemoryDataset(samples), solver=[generate()])

    print(f"→ évaluation de {args.n} échantillons (peut être long : le swarm "
          "orchestre des agents réels)…")
    result = inspect_eval(task, model=model, limit=args.n)
    for r in result:
        print(f"\n[score] {r.model} : {r.status} "
              f"| accuracy={r.results.score.accuracy:.2f} "
              f"({r.results.score.total_answered} réponses)")


if __name__ == "__main__":
    main()
