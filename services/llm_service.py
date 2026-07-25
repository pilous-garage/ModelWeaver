"""Service llm_manager : orchestration LLM, chat, modèles locaux.

Usage depuis les handlers daemon :
    from services.llm_service import list_models, recommend, chat, capabilities, ...
"""
from typing import Any, Dict


def list_models(params: dict) -> dict:
    from services.api._shared import _get_llm
    llm = _get_llm()
    seed = params.get("seed", False)
    if seed:
        from modules.llm_manager.llm_manager import seed_providers, seed_models, seed_provider_models
        seed_providers(llm)
        seed_models(llm)
        seed_provider_models(llm)
    return llm.list_models(params.get("provider_ref"))


def recommend(params: dict) -> dict:
    from services.api._shared import _get_llm
    llm = _get_llm()
    return llm.what_to_recommend(params.get("use_case"), params.get("technical_level"))


def chat(params: dict) -> dict:
    from services.api._shared import _get_bridge
    bridge = _get_bridge()
    return bridge.chat(
        messages=params["messages"],
        provider_ref=params.get("provider_ref"),
        model_ref=params.get("model_ref"),
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        stream=False,
    )


def chat_stream(params: dict, wfile) -> None:
    from services.api._shared import _get_bridge
    bridge = _get_bridge()
    bridge.chat_stream(
        messages=params["messages"],
        provider_ref=params.get("provider_ref"),
        model_ref=params.get("model_ref"),
        temperature=params.get("temperature"),
        max_tokens=params.get("max_tokens"),
        wfile=wfile,
    )


def capabilities(params: dict) -> dict:
    from services.api._shared import _get_bridge
    bridge = _get_bridge()
    return bridge.get_model_capabilities(params["provider_ref"], params["model_ref"])


def bridge_status(params: dict) -> dict:
    from services.api._shared import _get_bridge
    bridge = _get_bridge()
    return bridge.get_status(params.get("provider_ref"))


def context_probe(params: dict) -> dict:
    from services.api._shared import _get_bridge
    bridge = _get_bridge()
    return bridge.context_probe(params["provider_ref"], params["model_ref"])


def context_history(params: dict) -> Dict[str, Any]:
    from services.api._shared import _get_llm
    llm = _get_llm()
    return {"history": llm.get_context_history(
        params.get("provider_ref"),
        params.get("model_ref"),
        limit=params.get("limit", 10),
    )}


def local_list(_params: dict) -> dict:
    from services.api._shared import _get_llm
    from modules.llm_manager.local_engines import get_local_engine_manager
    mgr = get_local_engine_manager(_get_llm())
    return {"engines": mgr.list_engines()}


def local_start(params: dict) -> dict:
    from services.api._shared import _get_llm
    from modules.llm_manager.local_engines import get_local_engine_manager
    mgr = get_local_engine_manager(_get_llm())
    return mgr.start_engine(params["engine"])


def local_stop(params: dict) -> dict:
    from services.api._shared import _get_llm
    from modules.llm_manager.local_engines import get_local_engine_manager
    mgr = get_local_engine_manager(_get_llm())
    return mgr.stop_engine(params["engine"])


def local_models(params: dict) -> dict:
    from services.api._shared import _get_llm
    from modules.llm_manager.local_engines import get_local_engine_manager
    mgr = get_local_engine_manager(_get_llm())
    return {"models": mgr.list_models(params["engine"])}
