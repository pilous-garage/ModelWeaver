"""Loop — Boucle LLM générique avec tool calling.

Appelle le LLM en boucle : à chaque step, le LLM répond avec du texte
ou des ``tool_calls``. Si tool_calls, exécute chaque outil et nourrit
le résultat au LLM au step suivant. S'arrête quand le LLM répond en
texte (finish_reason=stop) ou qu'une condition de sortie est atteinte.

Usage::

    from agents_catalogue.lib.llm.loop import run
    from agents_catalogue.lib.llm.tool import Registry

    registry = Registry()
    registry.add_fn("read_file", read_file_fn, "Lit un fichier", {
        "path": {"type": "string"}
    })

    result = run(
        request="Analyse le fichier src/main.py",
        system_prompt="Tu es un assistant qui utilise des outils.",
        tools=registry,
        max_steps=20,
    )
    # result = {"signal": "loop_end", "output": "...", "steps": 5, ...}

Inspiré de : opencode packages/opencode/src/session/prompt.ts (runLoop)
"""

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from .tool import Registry

logger = logging.getLogger("modelweaver.llm.loop")


@dataclass
class LoopConfig:
    """Configuration de la boucle LLM."""
    provider_ref: str = ""
    model_ref: str = ""
    max_steps: int = 30
    temperature: float = 0.7
    max_tokens: int = 4096
    global_timeout: float = 0.0          # 0 = pas de limite
    exclude_providers: Set[str] = field(default_factory=set)
    exclude_models: Set[str] = field(default_factory=set)
    break_on_signals: List[str] = field(default_factory=list)
    break_on_counts: Dict[str, int] = field(default_factory=dict)
    auto_assign_llm: bool = True
    fallback_on_error: bool = True


@dataclass
class Turn:
    """Un step de la boucle : appel LLM + exécution des outils."""
    step: int
    response_text: str = ""
    tool_calls: List[Dict] = field(default_factory=list)
    results: List[Dict] = field(default_factory=list)
    error: str = ""


@dataclass
class LoopResult:
    """Résultat final de la boucle."""
    signal: str = "loop_end"             # loop_end | max_steps | error | global_timeout | break_signal | break_count
    output: str = ""
    steps: int = 0
    turns: List[Turn] = field(default_factory=list)
    error: str = ""
    exit_code: int = 0
    provider_ref: str = ""
    model_ref: str = ""


def _default_bridge(cat=None, km=None):
    """Bridge lazy, utilisable sans catalogue."""
    from modules.llm_manager.bridges import BridgeRegistry
    return BridgeRegistry(cat=cat, km=km)


def _auto_assign(cfg: LoopConfig):
    """Auto-assigne un LLM si non spécifié."""
    if cfg.provider_ref:
        return cfg.provider_ref, cfg.model_ref
    try:
        from modules.sql.db import CatalogueDB
        from modules.llm_manager.llm_manager import LLMManager
        llm = LLMManager(CatalogueDB()).assign_llm(
            use_case="coding",
            exclude_providers=list(cfg.exclude_providers),
            exclude_models=list(cfg.exclude_models),
        )
        if llm:
            return llm.get("provider_ref", ""), llm.get("model_ref", "")
    except Exception:
        pass
    return "", ""


def _signal_match(signal_key: str, pattern_list: List[str]) -> bool:
    for pattern in pattern_list:
        if pattern == signal_key:
            return True
        if pattern.endswith(".*") and signal_key.startswith(pattern[:-1]):
            return True
    return False


def _count_match(fn_name: str, counts: Dict[str, int],
                 thresholds: Dict[str, int]) -> Optional[str]:
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


def run(
    request: str,
    system_prompt: Optional[str] = None,
    tools: Optional[Registry] = None,
    bundle_names: Optional[List[str]] = None,
    execute_fn: Optional[Callable[[str, dict], dict]] = None,
    ws: str = "",
    cfg: Optional[LoopConfig] = None,
) -> LoopResult:
    """Boucle LLM principale.

    Args:
        request: Prompt utilisateur (premier message).
        system_prompt: Message système optionnel.
        tools: Registry d'outils programmatiques.
        bundle_names: Noms de bundles YAML à charger.
        execute_fn: Fonction d'exécution personnalisée.
        ws: Workspace/home path.
        cfg: Configuration de la boucle.

    Returns:
        LoopResult avec signal, output, turns.
    """
    if cfg is None:
        cfg = LoopConfig()

    from modules.llm_manager.bridges import BridgeRegistry
    bridge = BridgeRegistry()

    p_ref, m_ref = _auto_assign(cfg)
    if not p_ref:
        p_ref = cfg.provider_ref
        m_ref = cfg.model_ref

    # Résoudre les outils
    if tools or bundle_names:
        from .resolver import resolve_tools, make_dispatcher
        openai_tools = resolve_tools(registry=tools, bundle_names=bundle_names)
        dispatcher = execute_fn or make_dispatcher(registry=tools, ws=ws)
    else:
        openai_tools = []
        dispatcher = None

    # Construire les messages
    messages: List[Dict] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": request})

    tool_counts: Dict[str, int] = {}
    turns: List[Turn] = []
    loop_start = time.time()
    excluded_providers: set = set(cfg.exclude_providers)
    excluded_models: set = set(cfg.exclude_models)
    consecutive_failures = 0

    for step in range(cfg.max_steps):
        # Timeout global
        if cfg.global_timeout > 0 and (time.time() - loop_start) >= cfg.global_timeout:
            return LoopResult(
                signal="global_timeout",
                output=f"Timeout de {cfg.global_timeout}s atteint",
                steps=step, turns=turns,
                exit_code=124,
                provider_ref=p_ref, model_ref=m_ref,
            )

        turn = Turn(step=step)

        # Appel LLM
        try:
            bridge_instance = bridge.get(p_ref)
            response = bridge_instance.chat(
                provider_ref=p_ref, model_ref=m_ref,
                messages=messages,
                tools=openai_tools if openai_tools else None,
                temperature=cfg.temperature,
                max_tokens=cfg.max_tokens,
            )
        except Exception as exc:
            err_str = str(exc)[:300]
            logger.warning("LLM call failed (step %d): %s", step, err_str)
            turn.error = err_str
            turns.append(turn)
            consecutive_failures += 1

            # Détection rate-limit → backoff progressif avant de changer de provider
            is_rate_limit = "[rate_limit]" in err_str or "rate_limit" in err_str.lower()

            if is_rate_limit and consecutive_failures <= 5:
                backoff = [2, 5, 10, 20, 30][consecutive_failures - 1]
                import time as _time
                logger.warning("Rate-limit, retrying in %ds (attempt %d/5)...",
                              backoff, consecutive_failures)
                _time.sleep(backoff)
                continue

            # Si 5 tentatives rate-limit épuisées → essayer un autre provider
            if is_rate_limit and cfg.auto_assign_llm:
                excluded_providers.add(p_ref)
                excluded_models.add(m_ref)
                try:
                    from modules.sql.db import CatalogueDB
                    from modules.llm_manager.llm_manager import LLMManager
                    llm = LLMManager(CatalogueDB()).assign_llm(
                        use_case="coding",
                        exclude_providers=list(excluded_providers),
                        exclude_models=list(excluded_models),
                    )
                    if llm:
                        p_ref = llm.get("provider_ref", "")
                        m_ref = llm.get("model_ref", "")
                        consecutive_failures = 0
                        import time as _time
                        _time.sleep(2)  # Pause avant retry
                        continue
                except Exception:
                    pass

            # Erreur non-rate-limit : essayer un autre provider si auto_assign activé
            if not is_rate_limit and cfg.auto_assign_llm and consecutive_failures < 5:
                excluded_providers.add(p_ref)
                excluded_models.add(m_ref)
                try:
                    from modules.sql.db import CatalogueDB
                    from modules.llm_manager.llm_manager import LLMManager
                    llm = LLMManager(CatalogueDB()).assign_llm(
                        use_case="coding",
                        exclude_providers=list(excluded_providers),
                        exclude_models=list(excluded_models),
                    )
                    if llm:
                        p_ref = llm.get("provider_ref", "")
                        m_ref = llm.get("model_ref", "")
                        consecutive_failures = 0
                        import time as _time
                        _time.sleep(2)
                        continue
                except Exception:
                    pass

            if cfg.fallback_on_error and consecutive_failures < 3:
                import time as _time
                _time.sleep(5 * consecutive_failures)
                continue

            return LoopResult(
                signal="error",
                output=f"LLM error ({p_ref}/{m_ref}): {err_str}",
                steps=step + 1, turns=turns, error=err_str,
                exit_code=1, provider_ref=p_ref, model_ref=m_ref,
            )

        tool_calls = getattr(response, "tool_calls", None) or []
        content = getattr(response, "content", "") or ""
        turn.response_text = content

        # Pas de tool_calls → le LLM a fini
        if not tool_calls:
            return LoopResult(
                signal="loop_end",
                output=content or "(empty)",
                steps=step + 1, turns=turns,
                exit_code=0,
                provider_ref=p_ref, model_ref=m_ref,
            )

        # Ajouter la réponse assistant avec tool_calls
        asst_msg = {"role": "assistant", "content": content or None}
        tc_list = []
        for tc in tool_calls:
            fn_name = tc.get("function", {}).get("name", "")
            fn_args = tc.get("function", {}).get("arguments", "{}")
            tc_entry = {
                "id": tc.get("id", f"call_{step}_{fn_name}"),
                "type": "function",
                "function": {"name": fn_name, "arguments": fn_args},
            }
            tc_list.append(tc_entry)
            tool_counts[fn_name] = tool_counts.get(fn_name, 0) + 1

            # Vérifier break_on_signals
            if _signal_match(f"tool_call.{fn_name}", cfg.break_on_signals):
                return LoopResult(
                    signal="break_signal",
                    output=f"Signal break: tool_call.{fn_name}",
                    steps=step + 1, turns=turns,
                    exit_code=0,
                    provider_ref=p_ref, model_ref=m_ref,
                )

            # Vérifier break_on_counts
            count_reason = _count_match(fn_name, tool_counts, cfg.break_on_counts)
            if count_reason:
                return LoopResult(
                    signal="break_count",
                    output=f"Count threshold: {count_reason}",
                    steps=step + 1, turns=turns,
                    exit_code=0,
                    provider_ref=p_ref, model_ref=m_ref,
                )

        if tc_list:
            asst_msg["tool_calls"] = tc_list
        messages.append(asst_msg)

        # Exécuter chaque outil
        for tc in tool_calls:
            fn_name = tc.get("function", {}).get("name", "")
            fn_args_raw = tc.get("function", {}).get("arguments", "{}")
            try:
                fn_args = json.loads(fn_args_raw) if isinstance(fn_args_raw, str) else fn_args_raw
            except json.JSONDecodeError:
                fn_args = {}

            tool_result = {"ok": False, "error": "invalid arguments"}
            if dispatcher:
                try:
                    tool_result = dispatcher(fn_name, fn_args)
                except Exception as exc:
                    tool_result = {"ok": False, "error": str(exc)}

            turn.results.append({"name": fn_name, "result": tool_result})
            messages.append({
                "role": "tool",
                "tool_call_id": tc.get("id", f"call_{step}_{fn_name}"),
                "content": json.dumps(tool_result, default=str)[:5000],
            })

            # Vérifier break_on_signals sur tool_finish
            if _signal_match(f"tool_finish.{fn_name}", cfg.break_on_signals):
                return LoopResult(
                    signal="break_signal",
                    output=f"Signal break: tool_finish.{fn_name}",
                    steps=step + 1, turns=turns,
                    exit_code=0,
                    provider_ref=p_ref, model_ref=m_ref,
                )

        turns.append(turn)

    return LoopResult(
        signal="max_steps",
        output=f"Limite de {cfg.max_steps} étapes atteinte",
        steps=cfg.max_steps, turns=turns,
        exit_code=1,
        provider_ref=p_ref, model_ref=m_ref,
    )
