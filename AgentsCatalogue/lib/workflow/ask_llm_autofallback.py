"""ask_llm_autofallback — wrapper LLM haute volée (module JUMEau d'ask_llm).

Gros wrapper : mêmes arguments que ask_llm/ask_new_llm, PLUS un LLM optionnel
à tester en premier, PLUS le type d'endpoint SDK (chat/completion/responses),
PLUS les options (agentic, streaming, temperature...). Retourne DEUX résultats
à la fois :
  - llm_used : le LLM finalement sélectionné (id_address, model_name,
    provider_name) — qu'il y ait eu changement (fallback) ou pas ;
  - response : la réponse du LLM.

Routage par api_type du provider (openai_compatible / anthropic / gemini /
cohere) : le bridge (DirectBridge) fait déjà ce dispatch dans chat(); le
wrapper sélectionne le LLM PUIS délègue au bridge → les différences d'API
entre providers sont absorbées par le bridge, pas par l'agent.

Module créé À CÔTÉ d'ask_llm (fonctions renommées) — l'existant n'est pas
modifié.
"""

from __future__ import annotations

import json
import re
import time
from typing import Any, Dict, List, Optional


def _agent_id_from_home(home: str) -> str:
    m = re.search(r"agent_home/(\d+)", home or "")
    return m.group(1) if m else ""


def _resolve_use_case(use_case: str) -> str:
    return (use_case or "coding").replace("_high", "")


def _log_proxy(home: str, kind: str, message: str) -> None:
    """Log dédié du wrapper ({home}/ask_llm_autofallback.log)."""
    try:
        from pathlib import Path
        p = Path(home) / "ask_llm_autofallback.log"
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {kind}: "
                     f"{message}\n")
    except Exception:
        pass


def _llm_info(provider_ref: str, model_ref: str) -> Dict[str, Any]:
    """Formate {id_address, model_name, provider_name, provider_ref, model_ref}
    pour la réponse. model_ref = ref COMPLÈTE (réutilisable) ; model_name =
    dernier segment (affichage). provider_ref = ref réutilisable."""
    id_address = -1
    try:
        from services.llm_allocation.address import resolve_address
        aid = resolve_address(provider_ref, model_ref)
        id_address = int(aid) if aid is not None else -1
    except Exception:
        pass
    return {
        "id_address": id_address,
        "model_name": model_ref.split("/")[-1] if model_ref else "",
        "model_ref": model_ref or "",
        "provider_name": provider_ref or "",
        "provider_ref": provider_ref or "",
    }


def _allocate(inputs: dict, home: str) -> dict:
    """Sélectionne un LLM : celui fourni (llm_selectionne) d'abord, sinon
    ask_llm (allocation scoring). Retourne {ok, provider_ref, model_ref}."""
    # LLM déjà sélectionné (l'agent reste sur le même modèle) ?
    llm_sel = inputs.get("llm_selectionne") or {}
    if isinstance(llm_sel, str):
        # "provider/model" → (provider, model)
        if "/" in llm_sel:
            p, m = llm_sel.split("/", 1)
            return {"ok": True, "provider_ref": p, "model_ref": m,
                    "from_selection": True}
        return {"ok": False, "error": f"llm_selectionne mal formé: {llm_sel}"}
    p_sel = llm_sel.get("provider_ref") or llm_sel.get("provider_name") or ""
    m_sel = llm_sel.get("model_ref") or llm_sel.get("model_name") or ""
    if p_sel and m_sel:
        return {"ok": True, "provider_ref": p_sel, "model_ref": m_sel,
                "from_selection": True}
    # ask_llm (allocation) avec les mêmes arguments.
    try:
        from services.skill_manager import call_skill
        r = call_skill("ask_llm", {
            "use_case": inputs.get("use_case", "coding"),
            "agent_id": inputs.get("agent_id", "") or _agent_id_from_home(home),
            "not_same_modele": inputs.get("not_same_modele"),
            "exclude_models": inputs.get("exclude_models"),
            "restrict_llm": inputs.get("restrict_llm"),
            "min_window": inputs.get("min_window", 0),
        }, home=home)
        if r.get("ok") and r.get("model_ref"):
            return {"ok": True, "provider_ref": r["provider_ref"],
                    "model_ref": r["model_ref"], "from_selection": False}
        return {"ok": False, "error": r.get("error", "ask_llm échoué")}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _call_bridge(p_ref: str, m_ref: str, messages: List[Dict[str, str]],
                 inputs: dict, agent_id: str = "") -> Dict[str, Any]:
    """Appelle le bridge via resilient_chat (retry + repli autre LLM).

    Le routage par api_type (openai/anthropic/gemini/cohere) est fait par
    DirectBridge ; resilient_chat ajoute le retry transitoire (timeout/429)
    avec repli sur un autre LLM alloué. Retourne {ok, response, tool_calls}.

    `tools` (liste de schémas OpenAI, optionnel) : si fournis, le LLM peut
    répondre par des tool_calls (transmis tels quels au caller — SWE-bench et
    autres benchmarks d'agents en dépendent)."""
    from modules.llm_manager.resilient import resilient_chat
    from modules.llm_manager.llm_manager import LLMManager
    from modules.sql.catalogue_repo import CatalogueDB
    temperature = float(inputs.get("temperature", 0.7) or 0.7)
    max_tokens = inputs.get("max_tokens") or None
    timeout = int(inputs.get("timeout", 90) or 90)
    # fallback resilient : désactivé si un restrict_llm est fourni — sinon
    # resilient_chat fait son PROPRE assign_llm (hors allowlist) et peut
    # choisir un modèle hors liste. Le wrapper gère le fallback (boucle
    # d'essais + exclusion) en respectant le restrict.
    fallback = bool(inputs.get("resilient", True)) and not inputs.get("restrict_llm")
    tools = inputs.get("tools") or None
    tool_choice = inputs.get("tool_choice") or None
    bridge = LLMManager(CatalogueDB()).get_bridge()
    kwargs: Dict[str, Any] = {"temperature": temperature}
    if max_tokens:
        kwargs["max_tokens"] = int(max_tokens)
    if tools:
        kwargs["tools"] = tools
    if tool_choice:
        kwargs["tool_choice"] = tool_choice
    resp = resilient_chat(
        p_ref, m_ref, messages,
        timeout=timeout, fallback=fallback,
        use_case=_resolve_use_case(inputs.get("use_case", "coding")),
        agent_id=agent_id or None, bridge=bridge,
        **kwargs,
    )
    content = (getattr(resp, "content", "") or "").strip()
    tool_calls = getattr(resp, "tool_calls", None) or None
    return {"ok": True, "response": content, "tool_calls": tool_calls,
            "fallbacks": getattr(resp, "fallbacks", 0),
            "model_used": getattr(resp, "model_used", m_ref),
            "provider_used": getattr(resp, "provider_used", p_ref)}


def _endpoint_payload(endpoint: str, prompt: str, system: str,
                      inputs: dict) -> List[Dict[str, str]]:
    """Construit les messages selon le type d'endpoint SDK.

    - chat : [{system?}, {user}] → chat/completions
    - completion : [{system?}, {user}] → le bridge mappe vers le même chat
      (la plupart des providers ne servent plus /v1/completions legacy).
    - responses : idem chat (le bridge Responses est côté endpoint HTTP).
    """
    msgs: List[Dict[str, str]] = []
    if system:
        msgs.append({"role": "system", "content": system})
    msgs.append({"role": "user", "content": prompt})
    return msgs


def ask_llm_autofallback(inputs: dict, home: str) -> dict:
    """WRAPPER LLM : sélectionne un LLM (fourni OU ask_llm), l'appelle, et
    retourne {ok, llm_used, response} + toutes les infos.

    Arguments (mêmes que ask_llm +) :
      - prompt (requis) : texte à générer
      - system           : prompt système optionnel
      - llm_selectionne  : {provider_ref|provider_name, model_ref|model_name}
                          ou "provider/model" — testé d'abord (l'agent reste
                          sur le même modèle) ; échec → fallback ask_llm.
      - type_endpoint    : "chat" | "completion" | "responses" (défaut chat)
      - use_case, agent_id, not_same_modele, exclude_models, restrict_llm,
        min_window, temperature, max_tokens, stream (bool)
      - max_essais       : nb de tentatives (défaut 3) — un échec → re-alloc.

    Retourne TOUJOURS les deux résultats :
      - llm_used   : {id_address, model_name, provider_name}
      - response   : la réponse du LLM (str)
    """
    prompt = (inputs.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "llm_used": {}, "response": "",
                "error": "prompt requis"}
    system = inputs.get("system") or ""
    endpoint = (inputs.get("type_endpoint") or "chat").lower()
    max_essais = int(inputs.get("max_essais", 3) or 3)
    agent_id = inputs.get("agent_id", "") or _agent_id_from_home(home)
    _log_proxy(home, "request", f"ep={endpoint} prompt={prompt[:200]}")

    used = list(inputs.get("not_same_modele") or [])
    if isinstance(used, str):
        used = [m.strip() for m in used.split(",") if m.strip()]
    last_err = ""
    # Essai 0 : le LLM sélectionné (si fourni). Suivants : fallback ask_llm.
    for essai in range(max_essais):
        alloc = _allocate(dict(inputs, not_same_modele=used), home)
        if not alloc.get("ok"):
            last_err = alloc.get("error", "sélection échouée")
            break
        p_ref, m_ref = alloc["provider_ref"], alloc["model_ref"]
        if m_ref not in used:
            used = list(used) + [m_ref]
        _log_proxy(home, "alloc",
                   f"essai={essai} {p_ref}/{m_ref} (sel={alloc.get('from_selection')})")
        try:
            msgs = _endpoint_payload(endpoint, prompt, system, inputs)
            out = _call_bridge(p_ref, m_ref, msgs, inputs, agent_id)
            if not out.get("ok"):
                last_err = out.get("error", "bridge échoué")
                continue
            # resilient a pu basculer sur un autre modèle → le refléter.
            p_used = out.get("provider_used") or p_ref
            m_used = out.get("model_used") or m_ref
            _log_proxy(home, "response", out["response"][:500])
            return {
                "ok": True,
                "llm_used": _llm_info(p_used, m_used),
                "provider_ref": p_used, "model_ref": m_used,
                "response": out["response"],
                "tool_calls": out.get("tool_calls"),
                "fallback": not alloc.get("from_selection"),
                "resilient_fallbacks": out.get("fallbacks", 0),
                "endpoint": endpoint,
                "essais": essai + 1,
                "use_case": _resolve_use_case(inputs.get("use_case", "coding")),
            }
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            _log_proxy(home, "error", f"{p_ref}/{m_ref}: {last_err[:200]}")
            # échec → le défaillant est exclu, on re-alloc (un AUTRE modèle).
    return {"ok": False, "llm_used": {}, "response": "",
            "error": f"échec après {max_essais} essais: {last_err}",
            "endpoint": endpoint}


def ask_llm_autofallback_endpoint(inputs: dict, home: str) -> dict:
    """DOUBLE wrapper : précise l'endpoint SDK (chat/completion/responses)
    comme argument POSITIONNEL séparé, puis délègue à ask_llm_autofallback.

    Entrées : {endpoint: "chat"|"completion"|"responses", ...tous les args de
    ask_llm_autofallback}. Idéal pour les agents qui veulent expliciter le
    canal SDK : le bridge s'occupe des différences par provider.
    """
    endpoint = (inputs.get("endpoint") or inputs.get("type_endpoint")
                or "chat").lower()
    if endpoint not in ("chat", "completion", "responses"):
        return {"ok": False, "llm_used": {}, "response": "",
                "error": f"endpoint SDK inconnu: {endpoint} "
                         f"(chat | completion | responses)"}
    return ask_llm_autofallback(dict(inputs, type_endpoint=endpoint), home)


__skills__ = ["ask_llm_autofallback", "ask_llm_autofallback_endpoint"]
