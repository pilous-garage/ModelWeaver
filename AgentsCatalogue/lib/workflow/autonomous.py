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


def _auto_git_sync(home: str, phase: str) -> None:
    """Discipline git automatique sur le clone workspace/{projet} du membre.

    phase="pre"  : fetch + pull avant de commencer (voir le travail des autres).
    phase="post" : add + commit + push après l'action (publier son travail).

    Infrastructurelle : ne dépend pas du LLM. Ne casse pas si pas de clone.
    """
    import subprocess as _sp
    ws = _Path(home) / "workspace"
    if not ws.is_dir():
        return
    for clone in ws.iterdir():
        if not (clone / ".git").is_dir():
            continue
        c = ["git", "-C", str(clone)]
        try:
            _sp.run(c + ["config", "user.name", "agent-auto"], capture_output=True, timeout=10)
            _sp.run(c + ["config", "user.email", "agent-auto@modelweaver.local"], capture_output=True, timeout=10)
            if phase == "pre":
                _sp.run(c + ["fetch", "-q", "origin"], capture_output=True, timeout=30)
                # Branche distante courante (origin/HEAD), défaut master
                try:
                    head = _sp.run(c + ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
                                   capture_output=True, text=True, timeout=10)
                    remote_branch = head.stdout.strip().split("/", 1)[-1] or "master"
                except Exception:
                    remote_branch = "master"
                # checkout -B : resynchronise la branche locale sur la distante
                # (le pre-pull se fait AVANT l'action, rien à perdre localement)
                _sp.run(c + ["checkout", "-q", "-B", remote_branch,
                             f"origin/{remote_branch}"], capture_output=True, timeout=30)
            else:
                _sp.run(c + ["add", "-A"], capture_output=True, timeout=10)
                changed = _sp.run(c + ["diff", "--cached", "--quiet"],
                                  capture_output=True, timeout=10)
                if changed.returncode != 0:
                    _sp.run(c + ["commit", "-q", "-m", "auto-commit agent"], capture_output=True, timeout=10)
                    # Pull --rebase AVANT push : si d'autres membres ont avancé
                    # le repo central depuis notre fetch (push non-fast-forward),
                    # on réconcilie d'abord — sinon le push est rejeté et le
                    # membre boucle (98 tours observé sur coder-b).
                    _sp.run(c + ["fetch", "-q", "origin"], capture_output=True, timeout=30)
                    _sp.run(c + ["pull", "-q", "--rebase", "origin"], capture_output=True, timeout=30)
                    _sp.run(c + ["push", "-q", "origin", "HEAD"], capture_output=True, timeout=30)
        except Exception:
            continue


def _resolve_skill_candidates(fn_name: str) -> List[str]:
    """Candidats de noms de skill pour un tool call.

    Le LLM peut appeler `git_clone_v1` (nom d'outil) ou `git_clone` (naturel).
    On génère plusieurs candidats, du plus précis au plus large :
      git_clone_v1        -> git_clone@v1, git/clone@v1, clone@v1
    """
    base = fn_name
    out: List[str] = []

    # 1. Tel quel (déjà un nom de skill complet) : git/git_clone@v1
    if "@" in base or "/" in base:
        out.append(base)

    # 2. Conversion outil -> skill : git_clone_v1 -> git_clone@v1 -> git/clone@v1
    no_v = base.replace("_v1", "")
    named = no_v + "@v1"
    if named not in out:
        out.append(named)
    converted = no_v.replace("_", "/") + "@v1"
    if converted not in out:
        out.append(converted)

    # 3. Sans le préfixe de catégorie (dernier segment) : git/clone@v1 -> clone@v1
    if "/" in converted:
        last = converted.split("/")[-1]
        if last not in out:
            out.append(last)

    # 4. Nom sans suffixe : git_clone -> git/git_clone@v1 (tentative préfixe catégorie)
    if "_" in base and "@" not in base:
        parts = base.split("_")
        for k in range(1, len(parts)):
            cand = "/".join(parts[:k]) + "/" + "_".join(parts[k:]) + "@v1"
            if cand not in out:
                out.append(cand)

    return out


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
    _cat = CatalogueDB()
    # Logger FSM attaché au run parent (même fichier log/fsm_*.log du home) :
    # trace les appels LLM et tool calls pour le debugging du swarm.
    _fsm_log = None
    try:
        from AgentFrameWork.fsm_logger import FSMLogger
        _fsm_log = FSMLogger.attach(skill_home)
    except Exception:
        _fsm_log = None
    if _fsm_log is not None:
        _fsm_log.log("info", "fsm/start",
                     f"autonomous request={request[:80]}")
    # Bridge via LLMManager (façade, DirectBridge par défaut) avec KeyManager
    # (clés API en BDD) : sans lui, les providers fallback (google…) ne
    # trouvent jamais leur clé → échec de bascule.
    _km = None
    try:
        from modules.key_manager.key_manager import KeyManager
        from modules.sql.db import ModelWeaverDB
        _km = KeyManager(ModelWeaverDB())
    except Exception:
        pass
    from services.skill_manager import call_skill

    # Initialiser un shell pour le home (nécessaire pour les skills shell/exec)
    from services.agent_shell_manager import agent_shell_manager
    agent_shell_manager.init()
    _agent_id = _Path(skill_home).name
    if agent_shell_manager.get(_agent_id) is None:
        agent_shell_manager.get_or_create(agent_id=_agent_id, home_root=_Path(skill_home))

    # agent_id du home (agent_home/{agent_id}/…) — injecté dans chaque tool
    # call : les skills git/workspace en ont besoin (git_clone project_id+agent_id)
    # et le LLM ne le connaît pas.
    _aid_from_home = ""
    parts = _Path(skill_home).parts
    if "agent_home" in parts:
        _aid_from_home = str(parts[parts.index("agent_home") + 1])

    # LLMManager pour assignation et fallback (optionnel — si DB lockée, ignore)
    _llm_mgr = None
    try:
        from modules.llm_manager.llm_manager import LLMManager
        _llm_mgr = LLMManager(_cat, km=_km)
    except Exception:
        pass

    # Bridge actif via la façade (DirectBridge par défaut)
    try:
        if _llm_mgr is not None:
            bridge = _llm_mgr.get_bridge()
        else:
            from modules.llm_manager.direct_bridge import DirectBridge
            bridge = DirectBridge(cat=_cat, km=_km)
    except Exception:
        from modules.llm_manager.direct_bridge import DirectBridge
        bridge = DirectBridge(cat=_cat, km=_km)

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
    consecutive_failures = 0
    successful_tools = 0  # garde-fou : au moins un outil doit réussir
    loop_start = _time.time()

    # Auto-assigner un LLM si non spécifié (une fois pour toute la boucle)
    p_ref = provider_ref or ""
    m_ref = model_ref or ""
    if not p_ref:
        try:
            llm = _llm_mgr.assign_llm(use_case="coding", agent_id=_aid_from_home or None)
            if llm:
                p_ref = llm.get("provider_ref", "")
                m_ref = llm.get("model_ref", "")
        except Exception:
            pass

    excluded_providers: set = set()
    excluded_models: set = set()
    _original_retried = False  # re-try du provider original limité à 1 fois/membre
    # Compteurs séparés : tours d'outils réels vs tours d'échec/fallback LLM.
    # Un tour d'échec = l'appel LLM a raté (rate-limit, modèle mort, fallback)
    # sans exécuter d'outil. max_loops borne les DEUX ; on trace séparément
    # pour diagnostiquer et pour peupler model_call_log avec les vrais essais.
    _tool_rounds = 0
    _llm_fail_rounds = 0
    # Nombre de fois où le LLM a répondu en texte sans outils (no_action).
    # On re-sollicite UNE fois en rappelant d'utiliser les outils (certains
    # modèles de fallback répondent en texte au 1er tour), puis échec.
    _no_action_retries = 0
    # Échecs LLM consécutifs SANS outil réussi : si le fallback enchaîne les
    # modèles morts (chacun "réussi" par assign_llm mais échoue au vrai appel),
    # on doit abandonner vite — sinon coder-c/tester-a font 99 tours de
    # fallback mort après avoir déjà (ou pas) fini leur travail.
    _consec_llm_fails = 0

    for _round in range(max_loops):
        if global_timeout is not None and (_time.time() - loop_start) >= global_timeout:
            signals.append({"signal": "global_timeout", "stdout": f"Limite de {global_timeout}s atteinte", "exit_code": 124})
            return signals

        try:
            if _fsm_log is not None:
                _fsm_log.log("debug", "llm/call",
                             f"provider={p_ref} model={m_ref} "
                             f"round={_round} tools={len(tools)}")
            response = bridge.chat(p_ref, m_ref, messages, tools=tools, temperature=0.7,
                                   agent_id=_aid_from_home or None)
            if _fsm_log is not None:
                _fsm_log.log("debug", "llm/ok",
                             f"provider={p_ref} model={m_ref} "
                             f"tools={len(getattr(response, 'tool_calls', None) or [])}")
        except Exception as e:
            _llm_fail_rounds += 1
            _consec_llm_fails += 1
            err_str = str(e)[:200]
            if _fsm_log is not None:
                _fsm_log.log("warn", "llm/error",
                             f"provider={p_ref} model={m_ref} err={err_str[:100]}")
            # NE PAS ajouter un message tool orphelin (_api_error) à l'historique :
            # litellm rejette "Missing corresponding tool call for tool response
            # message" au tour suivant, ce qui casse le fallback provider.
            signals.append({"signal": "tool_finish", "tool": "_api_error",
                            "stdout": "", "stderr": err_str, "exit_code": 1})
            # Seuil d'échecs LLM consécutifs SANS outil réussi : si le pool de
            # fallback est saturé de modèles morts (chacun échoue au vrai
            # appel), on abandonne vite au lieu de faire 100 tours. Un membre
            # qui a déjà fait son travail (successful_tools>0) termine en
            # succès partiel ; sinon échec.
            if _consec_llm_fails >= 8:
                if successful_tools > 0:
                    signals.append({"signal": "loop_end",
                                    "stdout": (f"Travail effectué ({successful_tools} outils réussis) "
                                               f"mais pool LLM saturé: {err_str}"),
                                    "exit_code": 0})
                else:
                    signals.append({"signal": "llm_timeout",
                                    "stdout": f"pool LLM saturé après {_consec_llm_fails} échecs: {err_str}",
                                    "exit_code": 124})
                return signals
            # Fallback : en cas d'erreur API (rate_limit notamment), on bascule
            # sur un autre provider même si un provider explicite était fixé
            # (ex. quota gemini épuisé → groq). Le provider fautif est exclu.
            err_low = err_str.lower()
            # Catégorie BridgeError (auth/rate_limit/context/timeout/server/
            # unknown) : un MODÈLE MORT (deprecated, 404, clé invalide,
            # crédit insuffisant, erreur inconnue) ne redeviendra jamais bon →
            # on DOIT basculer sur un autre modèle. Seules les erreurs de
            # contexte (request too large) ne se résolvent pas par le fallback
            # si tous les modèles ont la même petite fenêtre.
            _err_cat = getattr(e, "category", None)
            _err_cat_val = getattr(_err_cat, "value", "") if _err_cat is not None else ""
            is_retryable = ("rate_limit" in err_low or "rate limit" in err_low
                            or "budget" in err_low or "quota" in err_low
                            or "429" in err_str or "404" in err_str
                            or "timeout" in err_low or "timed out" in err_low
                            or "deprecated" in err_low
                            or "not found" in err_low
                            or "insufficient" in err_low
                            or "credit" in err_low
                            or _err_cat_val in ("auth", "unknown")
                            # Gemini : functionCall rejoué sans thoughtSignature →
                            # erreur 400 côté API. Un retry sur un autre provider
                            # (ou une réponse régénérée) la résout.
                            or "thought_signature" in err_low
                            or "thoughtsignature" in err_low)
            # Type de limite (BridgeError enrichie) : un quota épuisé ne se
            # re-tente PAS sur place — on bascule immédiatement.
            limit_type = getattr(e, "limit_type", None)
            is_quota = (limit_type in ("quota", "daily_quota")
                        or "per-day" in err_low or "per day" in err_low
                        or ("quota" in err_low and "rate limit" not in err_low))
            # Backoff : un seul retry court (1s) avant de basculer. On ne veut
            # PAS s'attarder sur un modèle rate-limité (les retries 0/5/5s
            # faisaient perdre 10s × 50 tours = boucles interminables). On
            # échoue vite et on passe au modèle suivant via assign_llm.
            if is_retryable and not is_quota and "rate limit" in err_low:
                _time.sleep(1)
                try:
                    response = bridge.chat(p_ref, m_ref, messages, tools=tools, temperature=0.7,
                                           agent_id=_aid_from_home or None)
                    continue  # réponse obtenue, on traite les tools
                except Exception:
                    pass  # échoue vite → fallback provider
            if is_retryable and p_ref not in excluded_providers:
                # Rate-limit = PAR MODÈLE (RPM) : on exclut le modèle, pas le
                # provider — un provider a souvent plusieurs modèles fiables
                # (ex. google/gemini-3.5-flash-lite rate-limité → essayer
                # google/gemini-3.5-flash, google/gemini-2.5-flash…). On ne
                # blackliste le provider QUE si c'est un problème provider-wide
                # (auth/clé invalide, quota org épuisé).
                err_cat = getattr(e, "category", None)
                limit_type = getattr(e, "limit_type", None)
                err_cat_val = getattr(err_cat, "value", "") if err_cat is not None else ""
                provider_wide = (err_cat_val == "auth"
                                 or limit_type in ("quota", "daily_quota"))
                excluded_models.add(m_ref)
                if provider_wide:
                    excluded_providers.add(p_ref)
                # Demander un autre LLM au gestionnaire (assign_llm) : c'est la
                # seule source de vérité (respecte noretryuntil/unavailable et
                # exclut les providers/modèles déjà essayés). Pas de liste
                # hardcodée — ça multiplierait les appels et court-circuiterait
                # le LLMManager.
                switched = False
                if _llm_mgr:
                    # Taille approximative de l'historique (chars→tokens ~ /4) :
                    # le fallback ne doit pas choisir un modèle dont la fenêtre
                    # est trop petite (ex. groq/llama-3.1-8b = 8k) sinon on
                    # repart en "request too large" en boucle.
                    try:
                        _hist_tokens = sum(len(str(m.get("content", ""))) for m in messages) // 4
                    except Exception:
                        _hist_tokens = 0
                    for _try in range(6):
                        try:
                            llm = _llm_mgr.assign_llm(use_case="coding",
                                                      exclude_providers=list(excluded_providers),
                                                      exclude_models=list(excluded_models),
                                                      min_window=_hist_tokens,
                                                      agent_id=_aid_from_home or None)
                            if not llm:
                                break
                            np_ = llm.get("provider_ref", "")
                            nm_ = llm.get("model_ref", "")
                            if not np_ or np_ in excluded_providers:
                                if np_:
                                    excluded_providers.add(np_)
                                continue
                            # Pas de probe : on bascule directement, le tour
                            # suivant fera le vrai appel. S'il échoue (modèle
                            # mort), _mark_call_failed le met en repos et le
                            # fallback re-basculera — sans requête gaspillée.
                            p_ref, m_ref = np_, nm_
                            switched = True
                            break
                        except Exception:
                            break
                if switched:
                    continue
                # Pas de re-try long du provider original : si le fallback a
                # épuisé le pool, on échoue vite (llm_timeout) au lieu
                # d'attendre 40s — l'utilisateur préfère un échec rapide qu'une
                # boucle de backoff interminable.
            # Travail déjà effectué : si le membre a exécuté des outils avec
            # succès (ex. write_file + commit + push OK) et que seul l'appel
            # LLM de CONFIRMATION finale échoue (pool saturé), on considère le
            # travail comme fait — sinon coder-c refait 99 tours de fallback
            # mort après avoir déjà poussé son fichier.
            if successful_tools > 0:
                signals.append({"signal": "loop_end",
                                "stdout": (f"Travail effectué ({successful_tools} outils réussis) "
                                           f"mais confirmation LLM impossible: {err_str}"),
                                "exit_code": 0})
                return signals
            signals.append({"signal": "llm_timeout", "stdout": f"LLM error ({p_ref}/{m_ref}): {err_str}", "exit_code": 124})
            return signals

        tool_calls = getattr(response, "tool_calls", None)
        content = getattr(response, "content", "") or ""

        if not tool_calls and content:
            # Garde-fou : répondre en texte sans avoir exécuté AUCUN outil
            # (même en échec) = l'agent décrit au lieu d'agir → pas un succès.
            if successful_tools == 0:
                # Certains modèles de fallback répondent en texte au 1er tour
                # au lieu d'appeler les outils. On les re-sollicite UNE fois en
                # le rappelant explicitement, puis on échoue (no_action).
                if _no_action_retries < 1:
                    _no_action_retries += 1
                    if _fsm_log is not None:
                        _fsm_log.log("warn", "tool/no_action_retry",
                                     "LLM a répondu en texte, re-sollicitation")
                    messages.append({"role": "system",
                                     "content": ("Tu dois EXÉCUTER la tâche via les outils "
                                                 "(git_clone_v1, write_file_v1, git_commit_v1, "
                                                 "git_push_v1). Ne réponds pas en texte : appelle "
                                                 "directement un outil pour faire le travail.")})
                    continue
                signals.append({"signal": "no_action",
                                "stdout": "Réponse texte sans aucun outil exécuté — rien n'a été fait",
                                "exit_code": 1})
                return signals
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
            # Gemini exige la thoughtSignature quand on rejoue les functionCall.
            if tc.get("thoughtSignature"):
                tc_entry["thoughtSignature"] = tc["thoughtSignature"]
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
        _tool_rounds += 1 if tc_list else 0
        messages.append(asst_msg)

        for tc in tool_calls:
            fn_name = tc["function"]["name"]
            try:
                raw_args = json.loads(tc["function"]["arguments"])
            except Exception:
                raw_args = {}

            if _fsm_log is not None:
                _fsm_log.log("debug", "tool/call",
                             f"name={fn_name} args={raw_args}")

            # Résolution du nom de skill : plusieurs candidats (le nom peut
            # être tronqué par le LLM, ex. git_clone_v1 pour git/git_clone@v1).
            # L'agent_id du home est injecté (le LLM ne le connaît pas, les
            # skills git/workspace le requièrent).
            tool_result = None
            _injected = dict(raw_args)
            # L'agent_id du home est TOUJOURS forcé (le LLM l'invente souvent,
            # ex. "agent_388" ou un nom de membre) — les skills git/workspace
            # s'en servent pour le home et le clone.
            if _aid_from_home:
                _injected["agent_id"] = _aid_from_home
            candidates = _resolve_skill_candidates(fn_name)
            for cand in candidates:
                try:
                    tool_result = call_skill(cand, _injected, home=skill_home)
                    if isinstance(tool_result, dict) and not tool_result.get("error", "").startswith("skill"):
                        break
                except Exception:
                    tool_result = None
            if tool_result is None:
                tool_result = {"ok": False, "error": f"skill '{fn_name}' introuvable"}

            # Anti-boucle : trop d'échecs consécutifs → on arrête avec le
            # dernier résultat au lieu de tourner jusqu'à max_loops (le LLM
            # peut re-tenter un outil en échec en boucle, ex. docker sans clone).
            failed = (isinstance(tool_result, dict)
                      and (tool_result.get("ok") is False
                           or tool_result.get("status") in ("error", "failed")
                           or tool_result.get("exit_code") not in (None, 0)))
            consecutive_failures = consecutive_failures + 1 if failed else 0
            if not failed:
                successful_tools += 1
                # Un outil réussi = le membre avance → reset les échecs LLM
                # consécutifs (le pool n'est plus la cause du blocage).
                _consec_llm_fails = 0
            if _fsm_log is not None:
                _fsm_log.log("warn" if failed else "debug", "tool/" + ("error" if failed else "ok"),
                             f"name={fn_name} exit={tool_result.get('exit_code')} "
                             f"err={str(tool_result.get('error') or tool_result.get('stderr') or '')[:80]}")
            if consecutive_failures >= 4:
                signals.append({"signal": "tool_loop_break",
                                "stdout": f"{consecutive_failures} échecs d'outil consécutifs sur {fn_name}: {tool_result.get('stderr') or tool_result.get('error') or tool_result.get('stdout', '')}",
                                "exit_code": 1})
                return signals

            messages.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                             "name": fn_name, "content": json.dumps(tool_result, default=str)})

            finish_signal = {"signal": "tool_finish", "tool": fn_name, "stdout": tool_result.get("stdout", ""), "stderr": tool_result.get("stderr", ""), "exit_code": tool_result.get("exit_code", 1)}
            signals.append(finish_signal)

            if _signal_match(f"tool_finish.{fn_name}", break_list):
                return signals

    signals.append({"signal": "max_loops",
                    "stdout": f"Limite de {max_loops} boucles atteinte (tools={_tool_rounds}, échecs_llm={_llm_fail_rounds})",
                    "exit_code": 1})
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

    # Discipline git : pull avant d'agir (voir le travail des autres membres)
    _auto_git_sync(home, "pre")

    signals = _chat_with_tools(request, context, tools, max_loops, grouping,
                               break_on_signals, break_on_counts,
                               llm_timeout, global_timeout,
                               provider_ref, model_ref, home)

    # Discipline git : commit + push après l'action (publier son travail)
    _auto_git_sync(home, "post")

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