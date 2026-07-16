"""Résilience LLM — appel avec timeout + repli sur un autre LLM.

`resilient_chat` exécute un appel LLM dans un thread borné à `timeout`
secondes. Si le LLM ne répond pas (timeout) ou lève une erreur
réseau/auth/serveur, il demande un autre LLM au **gestionnaire de LLM**
(`LLMManager.assign_llm`) et réessaie, jusqu'à `max_retries` tentatives.

C'est le cœur du skill `llm/timeout_llm@v1` et de l'option `timeout`+
`fallback` de l'étape FSM `llm_call`.
"""
import threading
from typing import Any, Dict, List, Optional

from modules.llm_manager.base_bridge import (
    BridgeError, ErrorCategory,
)
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.llm_manager import LLMManager
from modules.sql.db import CatalogueDB, ModelWeaverDB
from modules.key_manager.key_manager import KeyManager


def _run_with_timeout(bridge: LiteLLMBridge, provider_ref: str, model_ref: str,
                      messages: List[Dict[str, str]], timeout: int,
                      kwargs: Dict[str, Any]):
    """Lance `bridge.chat` dans un thread ; lève TimeoutError si dépassé."""
    box: Dict[str, Any] = {}

    def target():
        try:
            box["r"] = bridge.chat(provider_ref=provider_ref,
                                   model_ref=model_ref,
                                   messages=messages, **kwargs)
        except BaseException as e:  # capture toute exception du thread
            box["err"] = e

    t = threading.Thread(target=target, daemon=True)
    t.start()
    t.join(timeout)
    if t.is_alive():
        raise TimeoutError(
            f"{provider_ref}/{model_ref} non réactif après {timeout}s")
    if "err" in box:
        e = box["err"]
        if isinstance(e, BridgeError):
            raise e
        raise BridgeError(ErrorCategory.UNKNOWN, str(e),
                          provider_ref, model_ref)
    if "r" not in box:
        raise BridgeError(ErrorCategory.UNKNOWN, "aucune réponse du LLM",
                          provider_ref, model_ref)
    return box["r"]


def resilient_chat(provider_ref: str, model_ref: str,
                   messages: List[Dict[str, str]], *,
                   timeout: int = 60, fallback: bool = True,
                   max_retries: int = 3, use_case: str = "coding",
                   bridge: Optional[LiteLLMBridge] = None,
                   cat=None, km=None, **kwargs) -> Any:
    """Appel LLM résilient avec repli sur un autre LLM.

    Retourne un ``ChatResponse`` augmenté des attributs :
      - ``provider_used`` / ``model_used`` : LLM réellement utilisé
      - ``fallbacks`` : nombre de replis effectués

    Lève ``BridgeError`` si tous les essais échouent.
    """
    if cat is None:
        cat = CatalogueDB()
    if km is None:
        km = KeyManager(ModelWeaverDB())
    if bridge is None:
        bridge = LiteLLMBridge(cat=cat, km=km)

    cur_p, cur_m = provider_ref, model_ref
    last_err: Optional[BridgeError] = None

    for attempt in range(max_retries):
        try:
            resp = _run_with_timeout(bridge, cur_p, cur_m, messages,
                                     timeout, kwargs)
        except TimeoutError as e:
            last_err = BridgeError(ErrorCategory.TIMEOUT, str(e),
                                   cur_p, cur_m)
        except BridgeError as e:
            last_err = e
        else:
            resp.provider_used = cur_p   # type: ignore[attr-defined]
            resp.model_used = cur_m      # type: ignore[attr-defined]
            resp.fallbacks = attempt     # type: ignore[attr-defined]
            return resp

        # Repli sur un autre LLM attribué par le gestionnaire de LLM.
        if not fallback or attempt == max_retries - 1:
            break
        cand = LLMManager(cat, km=km).assign_llm(
            exclude_provider=cur_p, exclude_model=cur_m, use_case=use_case)
        if not cand:
            break
        cur_p, cur_m = cand["provider_ref"], cand["model_ref"]

    if last_err is None:
        last_err = BridgeError(ErrorCategory.UNKNOWN,
                               "échec de l'appel résilient",
                               provider_ref, model_ref)
    raise last_err
