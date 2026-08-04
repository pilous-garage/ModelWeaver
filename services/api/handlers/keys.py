from pathlib import Path

from services.api._shared import _get_km, repo_root
from services.api.router import register

# ── Key Manager ─────────────────────────────────────────────────────────

def op_keys_set(params):
    provider_ref = params.get("provider_ref")
    api_key = params.get("api_key")
    if not provider_ref or not api_key:
        return {"status": "error", "error": "missing 'provider_ref' or 'api_key'"}
    km = _get_km()
    ref = km.set_key(
        provider_ref=provider_ref,
        api_key=api_key,
        api_base=params.get("api_base"),
        identity=params.get("identity", "default"),
        tag=params.get("tag", "paid"),
        grade=params.get("grade"),
    )
    from services.audit import audit
    audit("keys.set", provider_ref=provider_ref, ref=ref, ok=True)
    return {"status": "ok", "ref": ref}


def op_keys_get(params):
    km = _get_km()
    provider_ref = params.get("provider_ref", "")
    identity = params.get("identity", "default")
    try:
        key = km.get_key(
            provider_ref=provider_ref,
            identity=identity,
        )
    except Exception as e:
        from modules.key_manager.key_manager_module import KeyLockedError
        if isinstance(e, KeyLockedError):
            from services.audit import audit
            audit("keys.get", provider_ref=provider_ref, identity=identity, ok=False, error="locked")
            return {"status": "locked"}
        from services.audit import audit
        audit("keys.get", provider_ref=provider_ref, identity=identity, ok=False, error=str(e))
        raise
    from services.audit import audit
    if not key:
        audit("keys.get", provider_ref=provider_ref, identity=identity, ok=False, error="not_found")
        return {"status": "not_found"}
    audit("keys.get", provider_ref=provider_ref, identity=identity, ok=True)
    return {"status": "ok", "key": key}


def op_keys_set_lock(params):
    ref = params.get("ref")
    if not ref:
        return {"status": "error", "error": "missing 'ref'"}
    locked = bool(params.get("locked", True))
    km = _get_km()
    ok = km.set_lock(ref, locked)
    from services.audit import audit
    audit("keys.set_lock", ref=ref, locked=locked, ok=ok)
    return {"status": "ok" if ok else "error", "ref": ref, "locked": locked}


def op_keys_list(_params):
    km = _get_km()
    safe = km.list_keys()
    for k in safe:
        if not k.get("key_display"):
            k["key_display"] = "****"
    return {"keys": safe, "count": len(safe)}


def op_keys_delete(params):
    km = _get_km()
    ref = params.get("ref")
    provider_ref = params.get("provider_ref")
    from services.audit import audit
    if ref:
        ok = km.delete_key(ref)
        audit("keys.delete", ref=ref, ok=ok)
        return {"status": "ok" if ok else "error", "deleted": ok}
    if provider_ref:
        keys = km.list_keys()
        deleted = 0
        for k in keys:
            if k.get("provider_ref") == provider_ref:
                if km.delete_key(k["ref"]):
                    deleted += 1
        audit("keys.delete", provider_ref=provider_ref, deleted=deleted, ok=True)
        return {"status": "ok", "deleted": deleted}
    return {"status": "error", "error": "missing 'ref' or 'provider_ref'"}


def op_keys_onboard(params):
    from modules.key_manager.key_manager_module import Onboarder
    km = _get_km()
    onboarder = Onboarder(km)
    env_path = params.get("env_path", str(repo_root() / ".env"))
    count = onboarder.onboard_from_env(Path(env_path))
    from services.audit import audit
    audit("keys.onboard", env_path=env_path, imported=count, ok=count > 0)
    return {"status": "ok", "imported": count}


# ── Route registration ─────────────────────────────────────────────────

register("keys/set",         op_keys_set)
register("keys/get",         op_keys_get)
register("keys/list",        op_keys_list)
register("keys/delete",      op_keys_delete)
register("keys/set_lock",    op_keys_set_lock)
register("keys/onboard",     op_keys_onboard)
