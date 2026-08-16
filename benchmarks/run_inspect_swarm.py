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
    """Construit la tâche Inspect selon le benchmark demandé.

    - "factorial" : mini-dataset de démo trivial (pas de HF).
    - Tout autre nom : résolu dynamiquement dans inspect_evals (humaneval,
      mbpp, gsm8k, mmlu, gpqa, swe_bench, ...). Le module doit exposer une
      fonction du même nom que le module (convention inspect_evals).
    """
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

    # Benchmark inspect_evals générique : import dynamique du module, appel de
    # la fonction éponyme (convention), sinon la 1re fonction publique qui
    # retourne un inspect_ai.Task (ex. mmlu → mmlu_0_shot, gpqa → gpqa_diamond).
    # SWE-bench nécessite un sandbox docker (il exécute les tests du repo).
    import importlib
    try:
        mod = importlib.import_module(f"inspect_evals.{bench}")
    except ImportError as e:
        raise ValueError(
            f"benchmark inconnu: {bench} — n'est pas un module inspect_evals "
            f"(trouvés: humaneval, mbpp, factorial, + tous inspect_evals). "
            f"Détail: {e}")
    if bench in ("swe_bench", "swe_lancer"):
        fn = getattr(mod, bench, None)
        if callable(fn):
            return fn(sandbox_type="docker")
    fn = getattr(mod, bench, None)
    if not callable(fn):
        # convention fallback : 1re fonction publique qui retourne un Task
        from inspect_ai import Task
        fn = None
        for name in dir(mod):
            if name.startswith("_"):
                continue
            cand = getattr(mod, name)
            if callable(cand):
                try:
                    if isinstance(cand(), Task):
                        fn = cand
                        break
                except Exception:
                    continue
    if not callable(fn):
        raise ValueError(f"module inspect_evals.{bench} n'expose pas de "
                         f"fonction '{bench}()' (ni une fonction → Task)")
    return fn()


def list_inspect_evals() -> list:
    """Liste les benchmarks inspect_evals importables (modules avec une
    fonction éponyme). Retourne les noms triés."""
    from pathlib import Path
    pkg = Path(_venv_inspect_evals_dir())
    out = []
    if not pkg.is_dir():
        return out
    for d in sorted(pkg.iterdir()):
        if not d.is_dir() or d.name.startswith(("_", ".")):
            continue
        init = d / "__init__.py"
        mod_file = d / f"{d.name}.py"
        if not init.exists() and not mod_file.exists():
            continue
        out.append(d.name)
    return out


def _venv_inspect_evals_dir() -> str:
    """Chemin du package inspect_evals (venv-bench)."""
    import os
    repo = Path(__file__).resolve().parent.parent
    venv = repo / ".venv-bench" / "lib"
    if venv.is_dir():
        for py in sorted(venv.iterdir()):
            p = py / "site-packages" / "inspect_evals"
            if p.is_dir():
                return str(p)
    return str(repo / ".venv-bench" / "lib" / "python3.12"
               / "site-packages" / "inspect_evals")


def main() -> None:
    import sys
    sys.path.insert(0, str(REPO))
    from inspect_ai import eval as inspect_eval
    from inspect_ai.model import get_model

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8770)
    parser.add_argument("--n", type=int, default=5,
                        help="nb de problèmes (limit). Alias --max-prompts.")
    parser.add_argument("--max-prompts", type=int, default=0,
                        help="max de prompts (borne durée alternative à --n).")
    parser.add_argument("--bench", default="humaneval",
                        help="benchmark inspect_evals (humaneval, mbpp, gsm8k, "
                             "mmlu, gpqa, swe_bench... — tout module inspect_evals)")
    parser.add_argument("--bench-list", action="store_true",
                        help="liste les benchmarks inspect_evals dispo et sort.")
    parser.add_argument("--proxy", action="store_true",
                        help="utilise le PROXY (modèle mw-proxy) au lieu du "
                             "swarm complet : prompt→ask_llm_autofallback→"
                             "réponse. Diagnostic : si proxy ≈ 100% et swarm=0 "
                             "→ problème de conception prompt/réponse du swarm.")
    parser.add_argument("--restrict-llm", default="",
                        help="allowlist de modèles pour le proxy "
                             "(ex. 'groq/llama-3.3-70b-versatile,"
                             "opencode-zen/mimo-v2.5-free')")
    parser.add_argument("--sandbox", default="local",
                        help="sandbox inspect_ai (local | docker ; défaut local — "
                             "pas de plugin docker compose requis)")
    parser.add_argument("--sample-shuffle", type=int, default=0,
                        help="shuffle les samples avant de prendre les N premiers "
                             "(seed entier > 0 ; 0 = ordre du dataset). HumanEval "
                             "a 164 problèmes — limit prend les X après shuffle.")
    parser.add_argument("--max-parallel", type=int, default=20,
                        help="samples exécutés en parallèle par Inspect (défaut "
                             "20 — le swarm a 8 codeurs + max_concurrent 20)")
    parser.add_argument("--time-limit", type=int, default=5400,
                        help="limite de temps par sample en secondes (défaut "
                             "5400 = 90 min — le swarm met 5-30 min par sample)")
    args = parser.parse_args()

    if args.bench_list:
        print("Benchmarks inspect_evals disponibles :")
        for b in list_inspect_evals():
            print(f"  {b}")
        return

    # --max-prompts alias de --n (borne "max de prompts").
    n = args.max_prompts or args.n

    base_url = f"http://127.0.0.1:{args.port}/v1"
    token = _api_token(args.port)
    if not token:
        print("⚠️  token API introuvable (~/.modelweaver/api.token) — le daemon "
              "tourne-t-il ?")
        return

    if args.proxy:
        _model_name = "mw-proxy"
        print(f"→ PROXY branché sur {base_url} (modèle {_model_name})")
    else:
        _model_name = "mw-swarm-build"
        print(f"→ swarm branché sur {base_url} (modèle {_model_name})")
    # Pas de model_args : le SDK openai 2.x injecté par inspect_ai 0.3.255
    # les transmet tels quels à AsyncOpenAI (bug de compatibilité) → TypeError.
    model = get_model(f"openai/{_model_name}",
                      base_url=base_url, api_key=token)

    task = _build_task(args.bench, n)
    # PHASE CANCELLATION préalable : annule les vieilles tâches du workspace
    # pour que le swarm ne traite QUE le benchmark (les tâches résiduelles
    # polluent le picker et détournent les greedy).
    _cancel_workspace("mw-llm-code",
                      reason="reset avant benchmark inspect (phase cancellation)")
    print(f"→ évaluation {args.bench} x{n} (sandbox={args.sandbox}, "
          f"max_samples={args.max_parallel}, time_limit={args.time_limit}s, "
          "peut être long : le swarm orchestre des agents réels)…")
    try:
        result = inspect_eval(task, model=model, limit=n,
                              sample_shuffle=args.sample_shuffle or None,
                              sandbox=args.sandbox,
                              max_samples=args.max_parallel,
                              time_limit=args.time_limit)
    except KeyboardInterrupt:
        print("\n→ interruption — phase cancellation…", flush=True)
        _cancel_workspace("mw-llm-code",
                          reason="benchmark inspect interrompu")
        return
    _print_report(result)
    # PHASE CANCELLATION UNIQUEMENT si l'évaluation a échoué (statut non
    # completed) ou sur interruption. Si elle a réussi, on laisse les sessions
    # et le swarm finir de travailler au besoin — annuler casserait un run qui
    # progresse.
    failed = any(getattr(r, "status", "completed") != "completed"
                 for r in result)
    if failed:
        _cancel_workspace("mw-llm-code",
                          reason="benchmark inspect en échec (phase cancellation)")


def _print_report(result) -> None:
    """Affiche le rapport propre depuis l'objet inspect result.

    Le format inspect a changé : les scores sont dans results.scores[]
    (EvalScore) avec metrics = {nom: {value}}. Ancien champ results.score est
    déprécié/None → l'affichage 'accuracy=nan' du runner original.
    """
    for r in result:
        st = getattr(r, "status", "?")
        nm = getattr(getattr(r, "eval", None), "model", "?")
        print(f"\n[rapport] {nm} : {st}")
        scored = unscored = 0
        scores = getattr(getattr(r, "results", None), "scores", None)
        if scores:
            for s in scores:
                name = getattr(s, "name", "?")
                metrics = getattr(s, "metrics", {}) or {}
                scored = getattr(s, "scored_samples", 0)
                unscored = getattr(s, "unscored_samples", 0)
                parts = [f"  {name} ({scored} samples)"]
                for mname, m in metrics.items():
                    val = m.get("value") if isinstance(m, dict) else getattr(m, "value", "?")
                    parts.append(f"    {mname} = {val:.4f}" if isinstance(val, float)
                                 else f"    {mname} = {val}")
                print("\n".join(parts))
        print(f"  total: {scored} répondus / {scored + unscored}")


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
