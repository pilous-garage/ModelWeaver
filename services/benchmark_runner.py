#!/usr/bin/env python3
"""benchmark_runner — Exécute un benchmark contre le swarm (swarm-as-llm).

Branche la team (via son workspace_id) sur l'endpoint /v1/chat/completions,
exécute le benchmark (Inspect si dispo, sinon un mini-benchmark intégré),
puis collecte le rapport : providers/modèles utilisés, nb_req, tok_in/tok_out,
coûts, durées (globale + par step), notes.

`restrict_llm` limite les modèles utilisables :
  - liste : ["google/gemini-2.5-flash", ...] → allowlist (exclusions des autres)
  - budget : {"tok_in": n, "tok_out": n, "nb_req": n, "dollars": n, "time_s": n}
            → le benchmark s'arrête quand une limite est atteinte.
"""

from __future__ import annotations

import json
import time
from typing import Any, Dict, List, Optional


# Workspace par défaut de la team llm-code.
LLM_CODE_WORKSPACE = "mw-llm-code"


def resolve_team_workspace(team: str) -> str:
    """Le workspace d'une team (depuis son manifest), ou défaut."""
    try:
        from services.team_spec import TeamSpec
        path = f"services/manifests/teams/{team}.team.yaml"
        spec = TeamSpec.from_yaml(path)
        return spec.workspace_id or LLM_CODE_WORKSPACE
    except Exception:
        return LLM_CODE_WORKSPACE


def _parse_restrict_llm(restrict_llm) -> Dict[str, Any]:
    """Normalise restrict_llm → {models: [...], budget: {...}}."""
    if not restrict_llm:
        return {"models": [], "budget": {}}
    if isinstance(restrict_llm, str):
        return {"models": [m.strip() for m in restrict_llm.split(",") if m.strip()],
                "budget": {}}
    if isinstance(restrict_llm, list):
        return {"models": list(restrict_llm), "budget": {}}
    if isinstance(restrict_llm, dict):
        # {"models": [...]} ou un budget {tok_in:…} ou mixte
        models = restrict_llm.get("models", [])
        budget = {k: v for k, v in restrict_llm.items()
                  if k != "models" and v is not None}
        return {"models": models, "budget": budget}
    return {"models": [], "budget": {}}


def _budget_exceeded(budget: Dict[str, Any], usage: Dict[str, Any]) -> bool:
    """Vrai si l'usage cumulé dépasse une limite du budget."""
    for k, limit in budget.items():
        used = usage.get(k, 0)
        if used >= limit:
            return True
    return False


# ── mini-benchmark intégré (si Inspect n'est pas installé) ────────────────

MINI_BENCHMARKS = {
    # name → {"desc", "tasks": [{id, prompt, check}]}
    # check : callable(content) → bool (évalue la réponse du swarm)
}


def _mini_benchmark(name: str) -> Dict[str, Any]:
    """Retourne la définition d'un mini-benchmark connu, ou {}."""
    return {
        "factorial": {
            "desc": "écrit une fonction factorielle",
            "tasks": [
                {"id": "fact-1",
                 "prompt": "Écris une fonction Python `fact(n)` récursive qui "
                           "calcule la factorielle. Réponds avec le code.",
                 "check": lambda c: "def fact" in c and "return" in c},
                {"id": "fact-2",
                 "prompt": "Écris une fonction Python `fact(n)` itérative. "
                           "Réponds avec le code.",
                 "check": lambda c: "def fact" in c and "return" in c},
            ],
        },
        "fibonacci": {
            "desc": "écrit une fonction fibonacci",
            "tasks": [
                {"id": "fib-1",
                 "prompt": "Écris une fonction Python `fib(n)` récursive. "
                           "Réponds avec le code.",
                 "check": lambda c: "def fib" in c and "return" in c},
            ],
        },
    }.get(name, {})


def run_benchmark(team: str, benchmark_name: str,
                  restrict_llm: Any = None,
                  n: int = 3,
                  base_url: str = "http://127.0.0.1:8770/v1",
                  api_key: str = "",
                  per_task: bool = False) -> Dict[str, Any]:
    """Exécute un benchmark contre le swarm branché sur la team.

    Retourne le rapport complet (voir _build_report). ``per_task`` collecte
    les durées/coûts PAR STEP (décomposition du benchmark en tâches)."""
    restrict = _parse_restrict_llm(restrict_llm)
    workspace = resolve_team_workspace(team)
    t_start = time.monotonic()

    bench = _mini_benchmark(benchmark_name)
    tasks = bench.get("tasks", [])[:n] if bench else []
    if not tasks:
        return {"status": "error",
                "error": f"benchmark '{benchmark_name}' inconnu "
                         f"(disponibles: {list(MINI_BENCHMARKS)} or 'inspect')"}

    usage = {"nb_req": 0, "tok_in": 0, "tok_out": 0, "dollars": 0.0,
             "time_s": 0.0}
    per_provider: Dict[str, Any] = {}
    steps: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    for t in tasks:
        if _budget_exceeded(restrict["budget"], usage):
            notes.append({"type": "budget", "detail": "budget atteint, arrêt"})
            break
        step_t0 = time.monotonic()
        try:
            content, call = _chat(workspace, t["prompt"], base_url, api_key,
                                  restrict["models"])
        except Exception as e:  # noqa: BLE001
            steps.append({"task": t["id"], "status": "error", "error": str(e),
                          "duration_s": round(time.monotonic() - step_t0, 3)})
            notes.append({"type": "error", "task": t["id"], "detail": str(e)})
            continue
        ok = bool(t["check"](content))
        steps.append({"task": t["id"], "status": "pass" if ok else "fail",
                      "duration_s": round(time.monotonic() - step_t0, 3)})
        _merge_usage(usage, per_provider, call)
        notes.append({"type": "result", "task": t["id"], "ok": ok})

    usage["time_s"] = round(time.monotonic() - t_start, 3)
    report = _build_report(team, workspace, benchmark_name, restrict,
                           usage, per_provider, steps, notes, per_task)
    # LOG du résultat (timestampé, BDD + JSONL) pour l'historique des runs.
    try:
        from services.benchmark_results import log_result
        passed = sum(1 for s in steps if s["status"] == "pass")
        total = len(steps)
        log_result(benchmark_name, task="", score=report.get("score", 0.0),
                   passed=passed, total=total,
                   meta={"status": "ok", "team": team, "workspace": workspace,
                         "usage": usage, "steps": steps})
    except Exception:
        pass
    return report


# ── Benchmark PROXY (diagnostic prompt→réponse) ───────────────────────────

PROXY_BENCHMARKS = {
    "proxy-basic": {
        "desc": "Réponses directes au proxy (sans swarm) : prompt → LLM → réponse",
        "tasks": [
            {"id": "capitale",
             "prompt": "Quelle est la capitale de la France ? Réponds en un mot.",
             "check": lambda c: "paris" in (c or "").lower()},
            {"id": "calc",
             "prompt": "Combien font 7 × 8 ? Réponds juste par le nombre.",
             "check": lambda c: "56" in (c or "")},
            {"id": "capital-en",
             "prompt": "What is the capital of Japan? Answer in one word.",
             "check": lambda c: "tokyo" in (c or "").lower()},
            {"id": "lang",
             "prompt": "Quelle langue est parlée au Brésil ? Un mot.",
             "check": lambda c: "portugais" in (c or "").lower()
                                or "portuguese" in (c or "").lower()},
            {"id": "physics",
             "prompt": "Combien de secondes dans une minute ? Un nombre.",
             "check": lambda c: "60" in (c or "")},
        ],
    },
    "proxy-reasoning": {
        "desc": "Petits raisonnements (le benchmark swarm était à 0)",
        "tasks": [
            {"id": "r1",
             "prompt": "Si un train roule à 60 km/h pendant 30 minutes, quelle "
                       "distance parcourt-il ? Réponds avec le nombre en km.",
             "check": lambda c: "30" in (c or "")},
            {"id": "r2",
             "prompt": "Quel est le prochain nombre de la suite : 2, 4, 6, 8, ?",
             "check": lambda c: "10" in (c or "")},
            {"id": "r3",
             "prompt": "3 oeufs à 1.5 € chacun + 1 pain à 2 € = combien ? "
                       "Réponds avec le nombre en euros.",
             "check": lambda c: "6.5" in (c or "") or "6,5" in (c or "")},
            {"id": "r4",
             "prompt": "Inverse de la phrase : 'Le chat mange la souris'. "
                       "Réponds juste avec la phrase inversée.",
             "check": lambda c: ("souris" in (c or "").lower()
                                 and "chat" in (c or "").lower()
                                 and "mange" in (c or "").lower())},
        ],
    },
    "proxy-code": {
        "desc": "Génération de CODE en réponse texte (le swarm était à 0)",
        "tasks": [
            {"id": "fact",
             "prompt": "Écris une fonction Python `fact(n)` récursive qui "
                       "calcule la factorielle. Réponds avec le code.",
             "check": lambda c: "def fact" in (c or "")
                                and "return" in (c or "")
                                and "fact(n-1)" in (c or "")},
            {"id": "fib",
             "prompt": "Écris une fonction Python `fib(n)` qui renvoie le "
                       "n-ième nombre de Fibonacci. Réponds avec le code.",
             "check": lambda c: "def fib" in (c or "")
                                and "return" in (c or "")},
            {"id": "fizzbuzz",
             "prompt": "Écris une fonction Python `fizzbuzz(n)` qui imprime "
                       "Fizz pour les multiples de 3, Buzz pour 5, FizzBuzz "
                       "pour les deux. Réponds avec le code.",
             "check": lambda c: "def fizzbuzz" in (c or "")
                                and "% 3" in (c or "")
                                and "% 5" in (c or "")},
            {"id": "sort",
             "prompt": "Écris une fonction Python `tri_bulle(arr)` qui trie "
                       "une liste par tri à bulles. Réponds avec le code.",
             "check": lambda c: "def tri_bulle" in (c or "")
                                or "def bubble_sort" in (c or "")},
            {"id": "palindrome",
             "prompt": "Écris une fonction Python `est_palindrome(s)` qui "
                       "vérifie si une chaîne est un palindrome. Réponds avec "
                       "le code.",
             "check": lambda c: "def est_palindrome" in (c or "")
                                or "def is_palindrome" in (c or "")},
        ],
    },
}


def run_proxy_benchmark(benchmark_name: str = "proxy-basic",
                        n: int = 5,
                        restrict_llm: Any = None,
                        use_case: str = "chat") -> Dict[str, Any]:
    """Exécute le benchmark DIRECT sur le proxy (pas le swarm complet).

    Prompt → proxy_llm_fallback (ask_llm + bridge) → réponse → check.
    C'est le diagnostic pur : si le proxy répond juste et le benchmark swarm
    est à 0, le problème est la conception prompt/réponse du pipeline swarm
    (découpe/git/respond), pas l'allocation LLM.
    """
    restrict = _parse_restrict_llm(restrict_llm)
    bench = PROXY_BENCHMARKS.get(benchmark_name, {})
    tasks = bench.get("tasks", [])[:n] if bench else []
    if not tasks:
        return {"status": "error",
                "error": f"benchmark proxy '{benchmark_name}' inconnu "
                         f"(disponibles: {list(PROXY_BENCHMARKS)})"}
    t_start = time.monotonic()
    usage = {"nb_req": 0, "tok_in": 0, "tok_out": 0, "dollars": 0.0,
             "time_s": 0.0}
    per_provider: Dict[str, Any] = {}
    steps: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    from services.swarm_llm_manager import run_proxy_completion
    for t in tasks:
        step_t0 = time.monotonic()
        try:
            res = run_proxy_completion(t["prompt"], use_case=use_case,
                                       restrict_llm=restrict["models"])
        except Exception as e:  # noqa: BLE001
            steps.append({"task": t["id"], "status": "error", "error": str(e),
                          "duration_s": round(time.monotonic() - step_t0, 3)})
            notes.append({"type": "error", "task": t["id"], "detail": str(e)})
            continue
        if not res.get("ok"):
            steps.append({"task": t["id"], "status": "error",
                          "error": res.get("error", "proxy échoué"),
                          "duration_s": round(time.monotonic() - step_t0, 3)})
            notes.append({"type": "error", "task": t["id"],
                          "detail": res.get("error", "proxy échoué")})
            continue
        content = ""
        choices = res.get("choices") or []
        if choices:
            content = (choices[0].get("message", {}).get("content") or "")
        ok = bool(t["check"](content))
        steps.append({"task": t["id"], "status": "pass" if ok else "fail",
                      "duration_s": round(time.monotonic() - step_t0, 3),
                      "response": (content or "")[:120]})
        pr = res.get("proxy", {})
        _merge_usage(usage, per_provider, {
            "provider": pr.get("provider", "?"),
            "model": pr.get("model_real", "?"),
            "nb_req": 1,
            "tok_in": max(1, len(t["prompt"]) // 4),
            "tok_out": max(1, len(content) // 4),
            "dollars": 0.0,
        })
        notes.append({"type": "result", "task": t["id"], "ok": ok})

    usage["time_s"] = round(time.monotonic() - t_start, 3)
    passed = sum(1 for s in steps if s["status"] == "pass")
    total = len(steps)
    return {
        "status": "ok",
        "mode": "proxy",
        "benchmark": benchmark_name,
        "score": round(passed / total, 4) if total else 0.0,
        "notes": notes,
        "usage": usage,
        "providers": per_provider,
        "duration_s": usage["time_s"],
        "steps": steps,
    }


def _chat(workspace: str, prompt: str, base_url: str, api_key: str,
          allow_models: List[str]) -> tuple:
    """Appelle l'endpoint swarm-as-llm branché sur la team (workspace).

    Retourne (content, {provider, model, nb_req, tok_in, tok_out, dollars})."""
    import urllib.request

    payload = {
        "model": "mw-swarm-build",
        "messages": [{"role": "user", "content": prompt}],
        "workspace_id": workspace,
    }
    if allow_models:
        payload["restrict_llm"] = allow_models
    req = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Bearer {api_key}",
                 "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=180) as resp:
        body = json.loads(resp.read())
    choices = body.get("choices", [])
    content = choices[0]["message"]["content"] if choices else ""
    swarm = body.get("swarm", {})
    # usage simulé (l'endpoint ne renvoie pas encore les vrais tokens) —
    # on approxime depuis la longueur ; le vrai comptage viendra de
    # model_call_log/real_call_models.
    return content, {
        "provider": swarm.get("provider", "?"),
        "model": swarm.get("model", "?"),
        "nb_req": 1,
        "tok_in": max(1, len(prompt) // 4),
        "tok_out": max(1, len(content) // 4),
        "dollars": 0.0,
    }


def _merge_usage(usage: Dict[str, Any], per_provider: Dict[str, Any],
                 call: Dict[str, Any]) -> None:
    usage["nb_req"] += call.get("nb_req", 1)
    usage["tok_in"] += call.get("tok_in", 0)
    usage["tok_out"] += call.get("tok_out", 0)
    usage["dollars"] += call.get("dollars", 0.0)
    key = f"{call.get('provider')}/{call.get('model')}"
    p = per_provider.setdefault(key, {"nb_req": 0, "tok_in": 0, "tok_out": 0,
                                      "dollars": 0.0})
    p["nb_req"] += call.get("nb_req", 1)
    p["tok_in"] += call.get("tok_in", 0)
    p["tok_out"] += call.get("tok_out", 0)
    p["dollars"] += call.get("dollars", 0.0)


def _build_report(team, workspace, benchmark_name, restrict,
                  usage, per_provider, steps, notes, per_task) -> Dict[str, Any]:
    passed = sum(1 for s in steps if s["status"] == "pass")
    total = len(steps)
    return {
        "status": "ok",
        "benchmark": benchmark_name,
        "team": team,
        "workspace": workspace,
        "restrict_llm": restrict,
        "score": round(passed / total, 4) if total else 0.0,
        "notes": notes,
        "usage": usage,
        "providers": per_provider,
        "duration_s": usage.get("time_s", 0),
        "steps": steps if per_task else None,
    }
