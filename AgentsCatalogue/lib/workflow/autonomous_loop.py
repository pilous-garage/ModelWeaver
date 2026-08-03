"""workflow/autonomous_loop@v1 — boucle externe autour de autonomous.

Appelé depuis un workflow FSM (à la place de workflow/autonomous@v1) :

    - type: call
      fn: workflow/autonomous_loop@v1
      inputs:
        request: "Implémente la feature X"
        bundles: ["dev"]
        max_loops: 50        # boucle INTERNE (autonomous)
        max_iter: 10         # boucle EXTERNE (tentatives avec escalade LLM)

Principe :
  1. On exécute autonomous avec max_loops (boucle interne courte).
  2. Après chaque itération on mesure le PROGRÈS : fichiers modifiés dans le
     home, commits git, outils réussis.
  3. S'il y a eu du progrès → on relance (l'agent continue sur la lancée).
  4. Si PAS de progrès (2 itérations stables) → on ESCALADE le LLM : on exclut
     le modèle courant et on en demande un meilleur via assign_llm, puis on
     relance avec le nouveau LLM.
  5. On s'arrête quand le travail est terminé (loop_end succès) ou max_iter
     atteint.

Retour : un dict compatible avec autonomous (signal, stdout, exit_code).
"""

import time
from pathlib import Path
from typing import Any, Dict, List

from .autonomous import _chat_with_tools, _resolve_skill_candidates


def _work_progress(home: str) -> Dict[str, Any]:
    """Mesure le progrès : fichiers modifiés + commits git + clones.

    Infrastructurel : pas d'appel LLM, ne lève jamais."""
    prog = {"files_mtime": [], "git_commits": 0, "work_files": 0}
    try:
        root = Path(home)
        # Fichiers récents dans le work/ (écrits par les tools)
        workdir = root / "work"
        if workdir.is_dir():
            now = time.time()
            for f in workdir.rglob("*"):
                if f.is_file() and (now - f.stat().st_mtime) < 120:
                    prog["work_files"] += 1
                    prog["files_mtime"].append(f.stat().st_mtime)
        # Clones workspace + commits locaux
        ws = root / "workspace"
        if ws.is_dir():
            import subprocess as sp
            for clone in ws.iterdir():
                if not (clone / ".git").is_dir():
                    continue
                try:
                    r = sp.run(["git", "-C", str(clone), "log", "--oneline", "-1"],
                               capture_output=True, text=True, timeout=10)
                    if r.returncode == 0 and r.stdout.strip():
                        prog["git_commits"] += 1
                except Exception:
                    pass
    except Exception:
        pass
    return prog


def _saw_progress(prev: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    """Vrai si cur a plus de fichiers écrits ou de commits que prev."""
    if cur["git_commits"] > prev["git_commits"]:
        return True
    if cur["work_files"] > prev["work_files"]:
        return True
    # Nouveaux mtime plus récents que l'ancien max
    if cur["files_mtime"]:
        prev_max = max(prev["files_mtime"] or [0])
        if max(cur["files_mtime"]) > prev_max:
            return True
    return False


def exec(inputs: dict, home: str) -> dict:
    request = inputs.get("request", "")
    if not request:
        return {"signal": "error", "stdout": "", "stderr": "request required", "exit_code": 1}
    bundle_names = inputs.get("bundles", [])
    if not bundle_names:
        return {"signal": "error", "stdout": "", "stderr": "bundles required", "exit_code": 1}

    context = inputs.get("context", "")
    max_loops = int(inputs.get("max_loops", 50))
    max_iter = int(inputs.get("max_iter", 10))
    grouping = inputs.get("grouping", "none")
    break_on_signals = inputs.get("break_on_signals", [])
    break_on_counts = inputs.get("break_on_counts", {})
    llm_timeout = inputs.get("llm_timeout")
    global_timeout = inputs.get("global_timeout")
    provider_ref = inputs.get("provider_ref", "")
    model_ref = inputs.get("model_ref", "")

    # Logger FSM attaché (même fichier que le run parent)
    _fsm_log = None
    try:
        from AgentFrameWork.fsm_logger import FSMLogger
        _fsm_log = FSMLogger.attach(home)
    except Exception:
        _fsm_log = None

    # LLMManager pour l'escalade (assign_llm)
    _llm_mgr = None
    try:
        from modules.llm_manager.llm_manager import LLMManager
        _llm_mgr = LLMManager()
    except Exception:
        _llm_mgr = None

    from services.skill_manager import call_skill
    from services._common import mw_home
    _aid_from_home = ""
    parts = Path(home).parts
    if "agent_home" in parts:
        _aid_from_home = str(parts[parts.index("agent_home") + 1])

    # Discipline git : pull avant d'agir (voir le travail des autres)
    from .autonomous import _auto_git_sync
    _auto_git_sync(home, "pre")

    tools = []
    try:
        from .bundles import resolve as resolve_bundles
        tools = resolve_bundles(bundle_names)
    except Exception:
        pass
    if not tools:
        return {"signal": "error", "stdout": "", "stderr": f"aucun outil trouvé dans bundles {bundle_names}", "exit_code": 1}

    excluded_models: List[str] = []
    prev_progress: Dict[str, Any] = {}
    stall_count = 0
    last_result: Dict[str, Any] = {"signal": "loop_end", "stdout": "", "exit_code": 0}

    for iteration in range(max_iter):
        p_ref, m_ref = provider_ref, model_ref

        # Escalade LLM : si pas de progrès, exclure le modèle courant et en
        # demander un meilleur (ou au moins un autre dispo).
        if iteration > 0 and stall_count >= 1:
            if _llm_mgr is not None:
                try:
                    llm = _llm_mgr.assign_llm(
                        use_case="coding", agent_id=_aid_from_home or None,
                        exclude_models=excluded_models)
                    if llm:
                        p_ref = llm.get("provider_ref", p_ref)
                        m_ref = llm.get("model_ref", m_ref)
                        excluded_models.append(m_ref)
                        if _fsm_log is not None:
                            _fsm_log.log("info", "loop/escalate",
                                         f"itération {iteration}: escalade → {p_ref}/{m_ref}")
                except Exception:
                    pass

        cur = _work_progress(home)
        made_progress = _saw_progress(prev_progress, cur) if prev_progress else True
        prev_progress = cur

        if _fsm_log is not None:
            _fsm_log.log("info", "loop/iter",
                         f"itération {iteration}/{max_iter} progrès={made_progress} "
                         f"llm={p_ref}/{m_ref}")

        signals = _chat_with_tools(
            request, context, tools, max_loops, grouping,
            break_on_signals, break_on_counts, llm_timeout, global_timeout,
            p_ref, m_ref, home)

        last_result = signals[-1] if signals else {"signal": "loop_end", "stdout": "", "exit_code": 0}
        sig = last_result.get("signal", "")

        # Fin de travail propre → stop.
        if sig in ("loop_end", "tool_finish") and last_result.get("exit_code") == 0:
            break
        if sig == "global_timeout":
            break

        # Progrès dans CETTE itération ?
        after = _work_progress(home)
        if _saw_progress(cur, after):
            stall_count = 0
        else:
            stall_count += 1

        # 2 itérations sans progrès → escalade déjà faite en tête de boucle ;
        # 3 sans progrès → on abandonne (le LLM est bloqué, pas la suite).
        if stall_count >= 3:
            if _fsm_log is not None:
                _fsm_log.log("warn", "loop/stall",
                             f"3 itérations sans progrès, abandon")
            break

    # Discipline git : commit + push après l'action (publier son travail)
    _auto_git_sync(home, "post")

    return last_result


__skills__ = ["exec"]
