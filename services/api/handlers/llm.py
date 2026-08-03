import json
import sys
from pathlib import Path

from services.api._shared import _mw_dir, _get_mw, _get_cat, _get_km, _get_llm, _get_bridge
from services.api.router import register, register_streaming

# ── LLM Manager ─────────────────────────────────────────────────────────

def op_llm_models_list(params):
    llm = _get_llm()
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
    except Exception:
        has_key_refs = set()
    cat = _get_cat()
    no_key_refs = set()
    cur = cat.conn.execute(
        "SELECT ref FROM catalogue_providers WHERE provider_type IN ('ollama', 'builtin', 'local')")
    no_key_refs = {row[0] for row in cur.fetchall()}
    allowed_refs = has_key_refs | no_key_refs
    provider_ref = params.get("provider_ref")
    if provider_ref:
        if provider_ref not in allowed_refs:
            return {"models": [], "count": 0, "error": "no_api_key",
                    "message": f"Aucune clé API fournie pour le provider '{provider_ref}'"}
        models = llm.list_models(provider_ref=provider_ref)
    else:
        all_models = llm.list_models()
        models = [m for m in all_models if m.get("provider_ref") in allowed_refs]
    return {"models": models, "count": len(models)}


def op_llm_recommend(params):
    llm = _get_llm()
    use_case = params.get("use_case", "chat")
    technical_level = params.get("technical_level", "free")
    valid_use_cases = ("chat", "coding", "analysis", "writing")
    valid_levels = ("free", "paid", "local")
    if use_case not in valid_use_cases:
        return {"status": "error", "error": f"use_case must be one of {valid_use_cases}"}
    if technical_level not in valid_levels:
        return {"status": "error", "error": f"technical_level must be one of {valid_levels}"}
    result = llm.recommend(use_case=use_case, technical_level=technical_level)
    try:
        km = _get_km()
        has_key_refs = set(km.list_providers())
    except Exception:
        has_key_refs = set()
    cat = _get_cat()
    cur = cat.conn.execute(
        "SELECT ref FROM catalogue_providers WHERE provider_type IN ('ollama', 'builtin', 'local')")
    no_key_refs = {row[0] for row in cur.fetchall()}
    allowed_refs = has_key_refs | no_key_refs
    filt = [r for r in result.get("recommendations", [])
            if r.get("provider") in allowed_refs]
    result["recommendations"] = filt
    result["count"] = len(filt)
    return result


# ── LLM Bridge ──────────────────────────────────────────────────────────

def op_llm_chat(params):
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    messages = params.get("messages", [])
    if not provider_ref or not model_ref or not messages:
        return {"status": "error", "error": "provider_ref, model_ref et messages requis"}
    try:
        resp = bridge.chat(
            provider_ref=provider_ref,
            model_ref=model_ref,
            messages=messages,
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens"),
            system_prompt=params.get("system_prompt"),
            stream=False,
            agent_id=params.get("agent_id"),
        )
        tokens = 0
        if resp.usage:
            tokens = (resp.usage.get("prompt_tokens", 0) or 0) + (resp.usage.get("completion_tokens", 0) or 0)
        if tokens:
            from services.ratelimit import check_rate_limit
            try:
                check_rate_limit("llm/chat", "127.0.0.1", tokens=tokens)
            except Exception:
                pass
        return {
            "status": "ok",
            "content": resp.content,
            "model": resp.model,
            "finish_reason": resp.finish_reason,
            "usage": resp.usage,
        }
    except BridgeError as be:
        from modules.llm_manager.llm_manager_module import BridgeError
        return {
            "status": "error",
            "error": be.message,
            "category": be.category.value,
            "provider_ref": be.provider_ref,
            "model_ref": be.model_ref,
        }
    except Exception as e:
        return {"status": "error", "error": str(e), "category": "unknown"}


def op_auth_info(params):
    mw = _mw_dir()
    token = (mw / "api.token").read_text().strip()
    port = int((mw / "api.port").read_text().strip())
    return {"token": token, "port": port}


class StreamWriter:
    def __init__(self, wfile):
        self._wfile = wfile
        self._closed = False

    def send(self, event: str, data: dict):
        if self._closed:
            return
        payload = f"event: {event}\ndata: {json.dumps(data)}\n\n"
        self._wfile.write(payload.encode())
        self._wfile.flush()

    def error(self, msg: str, category: str = "unknown",
              provider_ref: str = "", model_ref: str = ""):
        self.send("error", {"error": msg, "category": category,
                            "provider_ref": provider_ref, "model_ref": model_ref})

    def done(self):
        if self._closed:
            return
        self.send("done", {"done": True})
        self._closed = True


def op_llm_chat_stream_sse(params, wfile):
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    messages = params.get("messages", [])
    sw = StreamWriter(wfile)
    char_count = 0
    try:
        if not provider_ref or not model_ref or not messages:
            sw.error("provider_ref, model_ref et messages requis")
            sw.done()
            return
        for chunk in bridge.chat_stream(
            provider_ref=provider_ref,
            model_ref=model_ref,
            messages=messages,
            temperature=params.get("temperature", 0.7),
            max_tokens=params.get("max_tokens"),
            system_prompt=params.get("system_prompt"),
        ):
            char_count += len(chunk)
            sw.send("delta", {"content": chunk})
    except BridgeError as be:
        from modules.llm_manager.llm_manager_module import BridgeError
        sw.error(be.message, be.category.value, be.provider_ref, be.model_ref)
    except Exception as e:
        sw.error(str(e), "unknown", provider_ref or "", model_ref or "")
    finally:
        sw.done()
        if char_count:
            tokens = max(1, char_count // 4)
            from services.ratelimit import check_rate_limit
            try:
                check_rate_limit("llm/chat/stream", "127.0.0.1", tokens=tokens)
            except Exception:
                pass


def op_llm_capabilities(params):
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    if not provider_ref or not model_ref:
        return {"status": "error", "error": "provider_ref et model_ref requis"}
    try:
        caps = bridge.get_capabilities(provider_ref, model_ref)
        return {"status": "ok",
                "context_window": caps.context_window,
                "max_output": caps.max_output,
                "cost_input_per_1k": caps.cost_input_per_1k,
                "cost_output_per_1k": caps.cost_output_per_1k,
                "supports_vision": caps.supports_vision,
                "supports_function_calling": caps.supports_function_calling,
                "mode": caps.mode}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_bridge_status(params):
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    try:
        if provider_ref:
            return bridge.health_check(provider_ref)
        providers = bridge.list_available_providers()
        return {"status": "ok",
                "bridge": "litellm",
                "providers": providers,
                "count": len(providers)}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_context_probe(params):
    bridge = _get_bridge()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    if not provider_ref or not model_ref:
        return {"status": "error", "error": "provider_ref et model_ref requis"}
    try:
        caps = bridge.get_capabilities(provider_ref, model_ref)
        effective = bridge.validator.get_effective_context(provider_ref, model_ref)
        return {"status": "ok",
                "provider_ref": provider_ref,
                "model_ref": model_ref,
                "context_window_announced": caps.context_window,
                "context_window_effective": effective or caps.context_window}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def op_llm_context_history(params):
    cat = _get_cat()
    provider_ref = params.get("provider_ref")
    model_ref = params.get("model_ref")
    limit = params.get("limit", 50)
    if provider_ref and model_ref:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log WHERE provider_ref=? AND model_ref=? "
            "ORDER BY created_at DESC LIMIT ?",
            (provider_ref, model_ref, limit))
    elif provider_ref:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log WHERE provider_ref=? "
            "ORDER BY created_at DESC LIMIT ?",
            (provider_ref, limit))
    else:
        cur = cat.conn.execute(
            "SELECT * FROM context_audit_log ORDER BY created_at DESC LIMIT ?",
            (limit,))
    cols = [d[0] for d in cur.description]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    return {"status": "ok", "logs": rows, "count": len(rows)}


def op_llm_local_list(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    return mgr.list_engines()


def op_llm_local_start(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.start(engine_ref)


def op_llm_local_stop(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.stop(engine_ref)


def op_llm_local_models(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.list_models(engine_ref)


def op_llm_local_models_grouped(params):
    """Modèles locaux groupés par format d'entrée (ex. GGUF → ollama,
    llama.cpp, LM Studio) + fichiers GGUF présents dans ~/.modelweaver/models."""
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    from modules.llm_manager.hf_catalogue import list_local_models
    mgr = get_local_engine_manager()
    result = mgr.list_models_grouped()
    result["local_gguf_files"] = list_local_models()
    return result


def op_llm_local_start_model(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    model_ref = params.get("model")
    hardware = params.get("hardware", "auto")
    if not engine_ref or not model_ref:
        return {"status": "error", "error": "paramètres engine et model requis"}
    return mgr.start_model(engine_ref, model_ref, hardware=hardware)


def op_llm_local_hardware_modes(params):
    """Modes matériel supportés par chaque moteur + état GPU détecté."""
    from modules.llm_manager.local_engines import ENGINE_SPECS
    out = {}
    for ref, spec in ENGINE_SPECS.items():
        out[ref] = {
            "name": spec.get("name"),
            "hardware_modes": spec.get("hardware_modes", []),
        }
    # Détection matériel
    try:
        import psutil
        import shutil
        gpu = shutil.which("nvidia-smi") or shutil.which("rocm-smi") \
            or any(Path("/sys/class/drm").glob("card*/device/gpu_busy_percent"))
    except Exception:
        gpu = None
    return {"status": "ok", "modes": out,
            "hardware": {"gpu": bool(gpu)}}


def op_llm_local_stop_model(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    model_ref = params.get("model")
    if not engine_ref or not model_ref:
        return {"status": "error", "error": "paramètres engine et model requis"}
    return mgr.stop_model(engine_ref, model_ref)


def op_llm_local_check_resources(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    model_ref = params.get("model")
    size_gb = params.get("size_gb")
    if not model_ref:
        return {"status": "error", "error": "paramètre model requis"}
    try:
        size_gb = float(size_gb) if size_gb else None
    except (TypeError, ValueError):
        size_gb = None
    return mgr.check_resources(model_ref, size_gb=size_gb)


def op_llm_local_stop_motor(params):
    from modules.llm_manager.llm_manager_module import get_local_engine_manager
    mgr = get_local_engine_manager()
    engine_ref = params.get("engine")
    if not engine_ref:
        return {"status": "error", "error": "paramètre engine requis"}
    return mgr.stop(engine_ref)


# ── HF Catalogue ───────────────────────────────────────────────────────

def op_llm_hf_search(params):
    from modules.llm_manager.hf_catalogue import hf_search
    query = params.get("query", "")
    limit = params.get("limit", 20)
    return hf_search(query=query, limit=limit)


def op_llm_hf_download(params):
    from modules.llm_manager.hf_catalogue import hf_download
    repo_id = params.get("repo_id")
    filename = params.get("filename")
    if not repo_id:
        return {"status": "error", "error": "paramètre repo_id requis"}
    return hf_download(repo_id, filename)


def op_llm_hf_status(params):
    from modules.llm_manager.hf_catalogue import hf_downloads_all, hf_download_status
    download_id = params.get("download_id")
    if download_id:
        return hf_download_status(download_id)
    return hf_downloads_all()


def op_llm_hf_associate(params):
    from modules.llm_manager.hf_catalogue import associate_model
    repo_id = params.get("repo_id")
    filename = params.get("filename")
    engine = params.get("engine")
    tag = params.get("tag")
    if not repo_id or not filename or not engine:
        return {"status": "error",
                "error": "paramètres repo_id, filename et engine requis"}
    return associate_model(repo_id, filename, engine, tag)


def op_llm_hf_local(params):
    from modules.llm_manager.hf_catalogue import list_local_models
    return {"status": "ok", "count": len(list_local_models()),
            "models": list_local_models()}


# ── Route registration ─────────────────────────────────────────────────

register("llm/models/list",       op_llm_models_list)
register("llm/recommend",         op_llm_recommend)
register("llm/chat",              op_llm_chat)
register("llm/chat/stream",       op_llm_chat)
register("llm/capabilities",      op_llm_capabilities)
register("llm/bridge/status",     op_llm_bridge_status)
register("llm/context/probe",     op_llm_context_probe)
register("llm/context/history",   op_llm_context_history)
register("llm/local/list",        op_llm_local_list)
register("llm/local/start",       op_llm_local_start)
register("llm/local/stop",        op_llm_local_stop)
register("llm/local/models",      op_llm_local_models)
register("llm/local/models/grouped", op_llm_local_models_grouped)
register("llm/local/start-model", op_llm_local_start_model)
register("llm/local/stop-model",  op_llm_local_stop_model)
register("llm/local/hardware-modes", op_llm_local_hardware_modes)
register("llm/local/check-resources", op_llm_local_check_resources)
register("llm/local/stop-motor",  op_llm_local_stop_motor)
register("llm/hf/search",         op_llm_hf_search)
register("llm/hf/download",       op_llm_hf_download)
register("llm/hf/status",         op_llm_hf_status)
register("llm/hf/associate",      op_llm_hf_associate)
register("llm/hf/local",          op_llm_hf_local)
register("auth/info",             op_auth_info)

register_streaming("llm/chat/stream", op_llm_chat_stream_sse)
