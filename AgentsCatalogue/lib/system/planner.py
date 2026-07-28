"""Planificateur de skills pour LLM haut niveau.

Fournit :
1. list_skills — liste structurée des skills disponibles (pour que le LLM
   choisisse quels appels faire)
2. execute_plan — exécute une liste d'actions choisies par le LLM
3. plan_status — récupère le statut d'un plan en cours

Le cycle type :
  1. Agent appelle list_skills pour obtenir la boîte à outils
  2. LLM reçoit la liste + contexte + tâche → retourne un plan d'actions
  3. Agent appelle execute_plan avec le plan → exécute chaque action
  4. Si pas fini, retourne à l'étape 1 (boucle)

Chaque action dans le plan est un dict :
  {"ref": "system.home.ls", "inputs": {"mount": "leader_workspace", "path": "."}}

Le plan peut contenir une condition "if" et une boucle "until" optionnelles.
"""

import json
from pathlib import Path
from typing import Any

from .. import get_func, list_all as lib_list_all


def list_skills(inputs: dict, home: str) -> dict:
    """Renvoie la liste complète des skills disponibles avec leurs
    descriptions, entrées et sorties — format optimisé pour LLM."""
    skills = lib_list_all()
    result = []
    for s in skills:
        func = get_func(s["ref"])
        if not func:
            continue
        # On construit un résumé lisible pour le LLM
        doc_lines = (func.__doc__ or "").strip().split("\n")
        description = doc_lines[0].strip() if doc_lines else s["func"]
        result.append({
            "ref": s["ref"],
            "description": description,
            "module": s["module"],
            "func": s["func"],
        })
    return {"skills": result, "count": len(result)}


def plan_execute(inputs: dict, home: str) -> dict:
    """Exécute un plan d'actions séquentielles choisi par le LLM.

    inputs attend :
      plan: list[dict]  — chaque dict a "ref" et "inputs"
      context: dict     — contexte partagé entre les appels (optionnel)
      stop_on_error: bool — arrêter au premier échec (défaut: True)

    Retourne un dict avec results (liste de résultats par action) et
    status (success si tout OK, partial si quelques-uns échoués, failed si erreur bloquante).
    """
    plan = inputs.get("plan", [])
    if not isinstance(plan, list):
        return {"error": "plan doit être une liste"}
    if not plan:
        return {"error": "plan vide"}

    context = inputs.get("context", {})
    stop_on_error = inputs.get("stop_on_error", True)
    results = []
    any_failed = False
    all_ok = True

    for i, action in enumerate(plan):
        if not isinstance(action, dict):
            results.append({"step": i, "error": f"action #{i} n'est pas un dict"})
            all_ok = False
            continue

        ref = action.get("ref", "")
        action_inputs = action.get("inputs", {}) or {}

        if not ref:
            results.append({"step": i, "error": "ref manquant"})
            all_ok = False
            continue

        # Injecte le contexte partagé dans les inputs (sans écraser)
        merged = {}
        merged.update(context)
        merged.update(action_inputs)

        try:
            result = get_func(ref)(merged, home)
            result["step"] = i
            result["ref"] = ref
            results.append(result)
            if result.get("ok") is False and "error" in result:
                any_failed = True
                if stop_on_error:
                    break
        except Exception as e:
            results.append({"step": i, "ref": ref, "error": str(e)})
            any_failed = True
            if stop_on_error:
                break

    if any_failed and all_ok:
        all_ok = False

    status = "success" if all_ok else ("partial" if any_failed else "success")
    return {"results": results, "status": status, "steps_executed": len(results)}


def plan_status(inputs: dict, home: str) -> dict:
    """Renvoie un résumé rapide des résultats d'un plan pour que le LLM
    décide s'il faut continuer, réessayer, ou terminer."""
    plan_results = inputs.get("results", []) or []
    if not plan_results:
        return {"status": "empty", "message": "aucun résultat de plan fourni"}

    errors = [r for r in plan_results if "error" in r]
    successes = [r for r in plan_results if not r.get("ok") is False]

    if not errors:
        return {"status": "all_ok", "steps": len(plan_results)}
    if len(errors) == len(plan_results):
        return {
            "status": "all_failed",
            "steps": len(plan_results),
            "errors": [e.get("error", "unknown") for e in errors],
        }
    return {
        "status": "partial_errors",
        "steps": len(plan_results),
        "errors": [e.get("error", "unknown") for e in errors],
        "successes": len(successes),
    }


__skills__ = ["list_skills", "plan_execute", "plan_status"]