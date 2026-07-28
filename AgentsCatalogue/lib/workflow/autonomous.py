"""workflow/autonomous@v1 — boucle LLM autonome avec signaux de sortie.

Appelé depuis un workflow FSM :
    - type: call
      fn: workflow/autonomous@v1
      inputs:
        request: "Implémente la feature X"
        bundles: ["dev", "workflow"]
        context: "informations supplémentaires"
        max_loops: 30

Retourne un dict avec signal + résultat :
    {"signal": "tool_finish", "tool": "git.lite.commit", "stdout": "...", "exit_code": 0}
    {"signal": "loop_end", "stdout": "Tâche terminée", "exit_code": 0}
    {"signal": "max_loops", "stdout": "...", "exit_code": 1}
"""

import json
import time
from pathlib import Path
from pathlib import Path as _Path
from typing import Any, Dict, List, Optional

import yaml as _yaml

from .bundles import resolve as resolve_bundles


def _signal_match(signal_key: str, pattern_list: List[str]) -> bool:
    """Vérifie si signal_key matche un pattern dans la liste."""
    for pattern in pattern_list:
        if pattern == signal_key:
            return True
        if pattern.endswith(".*") and signal_key.startswith(pattern[:-1]):
            return True
    return False


def _count_match(fn_name: str, counts: Dict[str, int], thresholds: Dict[str, int]) -> Optional[str]:
    """Vérifie si un tool a atteint son seuil de comptage."""
    for pattern, limit in thresholds.items():
        actual = 0
        if pattern.endswith(".*"):
            prefix = pattern[:-1]
            for k, v in counts.items():
                if k.startswith(prefix):
                    actual += v
        else:
            actual = counts.get(pattern, 0)
        if actual >= limit:
            return f"count_{pattern}:{actual}"
    return None


def _chat_with_tools(request: str, context: str, tools: List[Dict],
                     max_loops: int = 30, grouping: str = "none",
                     break_on_signals: Optional[List[str]] = None,
                     break_on_counts: Optional[Dict[str, int]] = None,
                     llm_timeout: Optional[float] = None,
                     global_timeout: Optional[float] = None,
                     provider_ref: str = "", model_ref: str = "",
                     skill_home: str = "/tmp") -> List[Dict]:
    import time as _time
    from pathlib import Path as _Path
    from modules.sql.db import CatalogueDB
    from modules.llm_manager.litellm_bridge import LiteLLMBridge
    _cat = CatalogueDB()
    bridge = LiteLLMBridge(cat=_cat)
    from services.skill_manager import call_skill

    # Initialiser un shell pour le home (nécessaire pour les skills shell/exec)
    from services.agent_shell_manager import agent_shell_manager
    agent_shell_manager.init()
    _agent_id = _Path(skill_home).name
    if agent_shell_manager.get(_agent_id) is None:
        agent_shell_manager.get_or_create(agent_id=_agent_id, home_root=_Path(skill_home))

    # LLMManager pour assignation et fallback (optionnel — si DB lockée, ignore)
    _llm_mgr = None
    try:
        from modules.llm_manager.llm_manager import LLMManager
        _llm_mgr = LLMManager(_cat)
    except Exception:
        pass

    grouping_hints = {
        "req-optimal": ("Jusqu'à 10 outils indépendants par réponse."),
        "tok-optimal": ("Un seul outil par réponse. Analyse avant chaque appel."),
    }
    hint = grouping_hints.get(grouping, "")
    role = "Tu exécutes les tâches UNIQUEMENT via les outils. Ne génère JAMAIS de code dans ta réponse. Appelle directement l'outil shell_exec_v1 pour écrire les fichiers."
    if context:
        role = f"Tu exécutes les tâches UNIQUEMENT via les outils. Contexte : {context}"
    system_msg = f"{role} {hint}" if hint else role
    messages = [
        {"role": "system", "content": system_msg},
        {"role": "user", "content": request},
    ]
    signals = []
    break_list = break_on_signals or []
    count_thresholds = break_on_counts or {}
    tool_counts: Dict[str, int] = {}
    loop_start = _time.time()

    # Auto-assigner un LLM si non spécifié (une fois pour toute la boucle)
    p_ref = provider_ref or ""
    m_ref = model_ref or ""
    if not p_ref:
        try:
            llm = _llm_mgr.assign_llm(use_case="coding")
            if llm:
                p_ref = llm.get("provider_ref", "")
                m_ref = llm.get("model_ref", "")
        except Exception:
            pass

    excluded_providers: set = set()
    excluded_models: set = set()

    for _round in range(max_loops):
        if global_timeout is not None and (_time.time() - loop_start) >= global_timeout:
            signals.append({"signal": "global_timeout", "stdout": f"Limite de {global_timeout}s atteinte", "exit_code": 124})
            return signals

        try:
            response = bridge.chat(p_ref, m_ref, messages, tools=tools, temperature=0.7)
        except Exception as e:
            err_str = str(e)[:200]
            # Ajouter l'erreur aux messages pour que le LLM la voie
            messages.append({"role": "tool", "tool_call_id": "_api_error",
                           "content": json.dumps({"error": err_str, "exit_code": 1})})
            signals.append({"signal": "tool_finish", "tool": "_api_error",
                           "stdout": "", "stderr": err_str, "exit_code": 1})
            # Fallback si le provider a été auto-assigné
            if not provider_ref and _llm_mgr:
                excluded_providers.add(p_ref)
                excluded_models.add(m_ref)
                try:
                    llm = _llm_mgr.assign_llm(use_case="coding",
                                              exclude_providers=list(excluded_providers),
                                              exclude_models=list(excluded_models))
                    if llm:
                        p_ref = llm.get("provider_ref", "")
                        m_ref = llm.get("model_ref", "")
                        continue
                except Exception:
                    pass
            signals.append({"signal": "llm_timeout", "stdout": f"LLM error ({p_ref}/{m_ref}): {err_str}", "exit_code": 124})
            return signals

        tool_calls = getattr(response, "tool_calls", None)
        content = getattr(response, "content", "") or ""

        if not tool_calls and content:
            signals.append({"signal": "loop_end", "stdout": content, "exit_code": 0})
            return signals
        if not tool_calls and not content:
            signals.append({"signal": "loop_end", "stdout": "réponse vide", "exit_code": 0})
            return signals

        asst_msg = {"role": "assistant", "content": content or ""}
        tc_list = []
        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            tc_entry = {"id": tc.get("id"), "type": "function", "function": {"name": fn_name, "arguments": tc["function"]["arguments"]}}
            tc_list.append(tc_entry)

            tool_counts[fn_name] = tool_counts.get(fn_name, 0) + 1
            call_signal = {"signal": "tool_call", "tool": fn_name, "args": tc["function"]["arguments"], "count": tool_counts[fn_name]}
            signals.append(call_signal)

            if _signal_match(f"tool_call.{fn_name}", break_list):
                return signals
            count_reason = _count_match(fn_name, tool_counts, count_thresholds)
            if count_reason:
                signals.append({"signal": count_reason, "stdout": f"Seuil atteint pour {fn_name}", "exit_code": 0})
                return signals

        if tc_list:
            asst_msg["tool_calls"] = tc_list
        messages.append(asst_msg)

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                raw_args = json.loads(tc["function"]["arguments"])
            except Exception:
                raw_args = {}

            conv_name = fn_name.replace("_v1", "@v1").replace("_", "/")

            try:
                tool_result = call_skill(conv_name, raw_args, home=skill_home)
            except Exception as e:
                tool_result = {"ok": False, "error": str(e)}

            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""), "content": json.dumps(tool_result, default=str)})

            finish_signal = {"signal": "tool_finish", "tool": fn_name, "stdout": tool_result.get("stdout", ""), "stderr": tool_result.get("stderr", ""), "exit_code": tool_result.get("exit_code", 1)}
            signals.append(finish_signal)

            if _signal_match(f"tool_finish.{fn_name}", break_list):
                return signals

    signals.append({"signal": "max_loops", "stdout": f"Limite de {max_loops} boucles atteinte", "exit_code": 1})
    return signals


def exec(inputs: dict, home: str) -> dict:
    request = inputs.get("request", "")
    if not request:
        return {"signal": "error", "stdout": "", "stderr": "request required", "exit_code": 1}

    bundle_names = inputs.get("bundles", [])
    if not bundle_names:
        return {"signal": "error", "stdout": "", "stderr": "bundles required", "exit_code": 1}

    context = inputs.get("context", "")
    max_loops = int(inputs.get("max_loops", 30))
    grouping = inputs.get("grouping", "none")
    break_on_signals = inputs.get("break_on_signals", [])
    break_on_counts = inputs.get("break_on_counts", {})
    llm_timeout = inputs.get("llm_timeout")
    global_timeout = inputs.get("global_timeout")
    provider_ref = inputs.get("provider_ref", "")
    model_ref = inputs.get("model_ref", "")

    tools = resolve_bundles(bundle_names)
    if not tools:
        return {"signal": "error", "stdout": "", "stderr": f"aucun outil trouvé dans bundles {bundle_names}", "exit_code": 1}

    signals = _chat_with_tools(request, context, tools, max_loops, grouping,
                               break_on_signals, break_on_counts,
                               llm_timeout, global_timeout,
                               provider_ref, model_ref, home)

    # Auto-commit/push si des fichiers ont été modifiés dans le workdir
    workdir = _Path(home) / "work"
    if workdir.exists() and (workdir / ".git").exists():
        import subprocess as _sp
        _sp.run(["git", "-C", str(workdir), "config", "user.name", "auto"], capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "config", "user.email", "auto@auto"], capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "checkout", "-b", "main", "origin/main"],
               capture_output=True, timeout=10)
        _sp.run(["git", "-C", str(workdir), "add", "."], capture_output=True, timeout=10)
        r = _sp.run(["git", "-C", str(workdir), "diff", "--cached", "--quiet"],
                   capture_output=True, timeout=10)
        if r.returncode != 0:
            _sp.run(["git", "-C", str(workdir), "commit", "-m", "auto-commit"],
                   capture_output=True, timeout=10)
            _sp.run(["git", "-C", str(workdir), "push", "origin", "main"],
                   capture_output=True, text=True, timeout=10)

    return signals[-1] if signals else {"signal": "loop_end", "stdout": "", "exit_code": 0}


__skills__ = ["exec"]