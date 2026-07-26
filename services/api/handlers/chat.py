from services.api.handlers.agents import _get_agent_db
from services.api.router import register

# ── Chat Session Manager ────────────────────────────────────────────────

def _chat_mgr():
    from services.agent_manager.service import AgentManager
    return AgentManager(db=_get_agent_db())


def op_chat_session_create(params):
    return _chat_mgr().create_chat_session(
        name=params.get("name"),
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
        system_prompt=params.get("system_prompt", ""),
        allow_read_others=bool(params.get("allow_read_others", False)),
    )


def op_chat_session_list(_params):
    return _chat_mgr().list_chat_sessions()


def op_chat_session_get(params):
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().get_chat_session(name)


def op_chat_session_delete(params):
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().delete_chat_session(name)


def op_chat_session_update(params):
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().update_chat_session(
        name=name,
        system_prompt=params.get("system_prompt"),
        provider_ref=params.get("provider_ref"),
        model_ref=params.get("model_ref"),
        allow_read_others=params.get("allow_read_others"),
    )


def op_chat_session_send(params):
    name = params.get("name")
    message = params.get("message")
    if not name or message is None:
        return {"status": "error", "error": "name et message requis"}
    mt = params.get("max_tokens")
    return _chat_mgr().chat_send(
        name=name, message=message,
        provider_ref=params.get("provider_ref", ""),
        model_ref=params.get("model_ref", ""),
        stream=bool(params.get("stream", False)),
        temperature=float(params.get("temperature", 0.7)),
        max_tokens=int(mt) if mt is not None else None,
    )


def op_chat_session_history(params):
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return _chat_mgr().get_chat_session(name)


def op_chat_session_read(params):
    name = params.get("name")
    other = params.get("other")
    if not name or not other:
        return {"status": "error", "error": "name et other requis"}
    return _chat_mgr().chat_read(name, other)


def op_chat_session_stream(params):
    from services.api.handlers.agents import op_agent_stream
    name = params.get("name")
    if not name:
        return {"status": "error", "error": "name requis"}
    return op_agent_stream({"name": name, "seq": int(params.get("seq", 0) or 0)})


# ── Route registration ─────────────────────────────────────────────────

register("chat/session/create",  op_chat_session_create)
register("chat/session/list",    op_chat_session_list)
register("chat/session/get",     op_chat_session_get)
register("chat/session/delete",  op_chat_session_delete)
register("chat/session/update",  op_chat_session_update)
register("chat/session/send",    op_chat_session_send)
register("chat/session/history", op_chat_session_history)
register("chat/session/read",    op_chat_session_read)
register("chat/session/stream",  op_chat_session_stream)
