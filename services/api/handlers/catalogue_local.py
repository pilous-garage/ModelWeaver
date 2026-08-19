"""Routes catalogue_local/* — référentiel méta des données (V2).

Architecture (spec local_catalogue_spec.md) :
  - LECTURES mode=ro pour tout le monde (zéro écriture) ; les process lourds
    passent en SQLite direct (pas de SQL par le daemon).
  - ÉCRITURES DONNÉES : token du writer catalogue (write_catalogue) — mini-
    batch par le consumer du buffer pour les imports externes.
  - ÉCRITURES AUTORISATIONS : token PRIVÉ (write_catalogue_priv) — tables
    global_local_privilege* / global_local_security_supervisor.
  - data_id = hash stable(ref) ; data_value_type (text[n]ch, string, int,
    uint, float, bool, date, timestamp, json, file, row(header=type,…)).

Routes lecture :
  catalogue_local/data_types/list
  catalogue_local/data_types/get                 {code}
  catalogue_local/data/list                      {type, namespace, page, sort, order, status}
  catalogue_local/data/list_recursive            {type, namespace}
  catalogue_local/data/get                       {type, ref|data_id}
  catalogue_local/data/search                    {type, q, tag_type, tag_value}
  catalogue_local/data/refresh                   {type, data_id, last_access}
  catalogue_local/tag_type/list                  {type}
  catalogue_local/tag/list                       {type, ref|data_id}
  catalogue_local/tag/by_value                   {type, tag_type, tag_value}
  catalogue_local/sharing/get                    {type, ref|data_id}
  catalogue_local/shared_default/list
  catalogue_local/namespace/list/list_all/get    {parent|prefix|ns}
  catalogue_local/path/list                      {scheme}
  catalogue_local/path/resolve                   {target}
  catalogue_local/priv/list/resolve/check        (famille SECURITY)

Routes écriture données (token write_catalogue) :
  catalogue_local/data_types/add/modify/delete   {token, code, …}
  catalogue_local/data/add|modify                {token, type, ref, name, namespace,
                                                  version, data_value_type, value,
                                                  ref_file, path, description, status, row}
  catalogue_local/data/delete                    {token, type, ref|data_id}
  catalogue_local/row/add_column                 {token, type, header, value_type}
  catalogue_local/tag_type/add/modify/delete     {token, type, tag_type, …}
  catalogue_local/tag/attach/detach              {token, type, ref|data_id, …}
  catalogue_local/sharing/set                    {token, type, ref|data_id, …}
  catalogue_local/shared_default/set             {token, type, tag_type, tag_value, …}
  catalogue_local/namespace/create/delete        {token, ns, …}
  catalogue_local/path/create                    {token, path_name, address, …}
  catalogue_local/buffer/push/process/retry      {token, ops|limit, external_tag}

Routes écriture autorisations (token PRIVÉ) :
  catalogue_local/priv/create/use/approve        {priv_token, …}
"""

import os
from typing import Any, Dict

from services.api.router import register

from modules.sql.catalogue_local import (
    CatalogueTypeNotFound, SHARING_LEVELS, SOURCE_VALUES,
)

# Token du writer catalogue (données). En prod, injecté par le service ; ici
# fallback env MW_CATALOGUE_WRITE_TOKEN.
WRITE_TOKEN = os.environ.get("MW_CATALOGUE_WRITE_TOKEN", "write_catalogue")

# Token du writer PRIVÉ (autorisations privileges/conditions).
PRIV_WRITE_TOKEN = os.environ.get("MW_CATALOGUE_PRIV_WRITE_TOKEN",
                                  "write_catalogue_priv")


def _get(mode: str = "ro", priv: bool = False):
    """Retourne l'instance LocalCatalogue (V2).

    mode=ro (défaut) : lecture seule. mode=w : writer catalogue (données).
    mode=w + priv=True : writer PRIVÉ (famille SECURITY)."""
    from modules.sql.catalogue_local import LocalCatalogue
    key = ("priv" if priv else "w") if mode == "w" else "ro"
    inst = getattr(_get, f"_{key}", None)
    if inst is None:
        inst = LocalCatalogue(mode=mode,
                              write_token=PRIV_WRITE_TOKEN if priv else WRITE_TOKEN)
        setattr(_get, f"_{key}", inst)
    return inst


def _token(params: dict) -> str:
    return params.get("token", "")


def _priv_token(params: dict) -> str:
    return params.get("priv_token") or params.get("token", "")


def _ref_or_id(params: dict) -> tuple:
    return (params.get("ref", ""), params.get("data_id"))


# ── data_type ────────────────────────────────────────────────────────────

def op_data_types_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "data_types": _get().list_data_types()}


def op_data_types_get(params: dict) -> Dict[str, Any]:
    code = params.get("code", "")
    for dt in _get().list_data_types():
        if dt["code"] == code:
            return {"status": "ok", "data_type": dt}
    return {"status": "error", "error": f"type inconnu: {code}"}


def op_data_types_add(params: dict) -> Dict[str, Any]:
    return _get("w").create_type(params.get("code", ""),
                                 params.get("description", ""),
                                 token=_token(params))


def op_data_types_modify(params: dict) -> Dict[str, Any]:
    code = params.get("code", "")
    db = _get("w")
    tok = _token(params)
    if "active" in params:
        db.activate_type(code, bool(params.get("active")), token=tok)
    return {"status": "ok", "code": code}


def op_data_types_delete(params: dict) -> Dict[str, Any]:
    return _get("w").delete_type(params.get("code", ""), token=_token(params))


# ── data ─────────────────────────────────────────────────────────────────

def op_data_add(params: dict) -> Dict[str, Any]:
    return _get("w").upsert(
        params.get("type", ""), params.get("ref", ""),
        name=params.get("name", ""), namespace=params.get("namespace", ""),
        version=params.get("version", "latest"),
        data_value_type=params.get("data_value_type", "json"),
        value=params.get("value", "{}"),
        ref_file=params.get("ref_file", ""), path=params.get("path", ""),
        description=params.get("description", ""),
        status=params.get("status", "active"),
        row=params.get("row"), token=_token(params))


def op_data_modify(params: dict) -> Dict[str, Any]:
    return op_data_add(params)


def op_data_delete(params: dict) -> Dict[str, Any]:
    ref, data_id = _ref_or_id(params)
    return _get("w").delete(params.get("type", ""), ref=ref, data_id=data_id,
                            token=_token(params))


def op_data_get(params: dict) -> Dict[str, Any]:
    try:
        e = _get().get(params.get("type", ""),
                       ref=params.get("ref", ""),
                       data_id=params.get("data_id"))
        return {"status": "ok", "data": e}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_data_list(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", **(_get().list(
            params.get("type", ""),
            namespace=params.get("namespace", ""),
            page=int(params.get("page", 1) or 1),
            page_size=int(params.get("page_size", 50) or 50),
            sort=params.get("sort", "name"),
            order=params.get("order", "asc"),
            status=params.get("status", "")))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_data_list_recursive(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", "items": _get().list_recursive(
            params.get("type", ""), namespace=params.get("namespace", ""))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_data_search(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", "items": _get().search(
            params.get("type", ""), q=params.get("q", ""),
            tag_type=params.get("tag_type", ""),
            tag_value=params.get("tag_value", ""))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_data_refresh(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", **_get().refresh(
            params.get("type", ""), params.get("data_id", 0),
            last_access=params.get("last_access", ""))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_row_add_column(params: dict) -> Dict[str, Any]:
    return _get("w").add_row_column(params.get("type", ""),
                                    params.get("header", ""),
                                    params.get("value_type", "string"),
                                    token=_token(params))


# ── tag_type / tag ───────────────────────────────────────────────────────

def op_tag_type_list(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", "tags": _get().list_tag_types(
            params.get("type", ""))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_tag_type_add(params: dict) -> Dict[str, Any]:
    return _get("w").tag_type_add(params.get("type", ""),
                                  params.get("tag_type", ""),
                                  params.get("tag_value_type", "text"),
                                  params.get("description", ""),
                                  token=_token(params))


def op_tag_type_modify(params: dict) -> Dict[str, Any]:
    return op_tag_type_add(params)


def op_tag_type_delete(params: dict) -> Dict[str, Any]:
    return _get("w").tag_type_delete(params.get("type", ""),
                                     params.get("tag_type", ""),
                                     token=_token(params))


def op_tag_attach(params: dict) -> Dict[str, Any]:
    ref, data_id = _ref_or_id(params)
    return _get("w").tag_attach(
        params.get("type", ""), ref=ref, data_id=data_id,
        tag_type=params.get("tag_type", ""),
        tag_value=params.get("tag_value"), token=_token(params))


def op_tag_detach(params: dict) -> Dict[str, Any]:
    ref, data_id = _ref_or_id(params)
    return _get("w").tag_detach(
        params.get("type", ""), ref=ref, data_id=data_id,
        tag_type=params.get("tag_type", ""),
        tag_value=params.get("tag_value"), token=_token(params))


def op_tag_list(params: dict) -> Dict[str, Any]:
    try:
        e = _get().get(params.get("type", ""),
                       ref=params.get("ref", ""),
                       data_id=params.get("data_id"))
        return {"status": "ok", "tags": e.get("tags", [])}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_tag_by_value(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", "items": _get().tags_by_value(
            params.get("type", ""), params.get("tag_type", ""),
            tag_value=params.get("tag_value"))}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


# ── sharing ──────────────────────────────────────────────────────────────

def op_sharing_get(params: dict) -> Dict[str, Any]:
    ref, data_id = _ref_or_id(params)
    try:
        sh = _get().get_sharing(params.get("type", ""), ref=ref, data_id=data_id)
        return {"status": "ok", "sharing": sh}
    except CatalogueTypeNotFound as ex:
        return {"status": "error", "error": str(ex)}


def op_sharing_set(params: dict) -> Dict[str, Any]:
    ref, data_id = _ref_or_id(params)
    return _get("w").set_sharing(
        params.get("type", ""), ref=ref, data_id=data_id,
        source=params.get("source", "perso"),
        source_url=params.get("source_url", ""),
        is_from_share=bool(params.get("is_from_share", False)),
        is_it_shared=bool(params.get("is_it_shared", False)),
        can_be_shared=params.get("can_be_shared", "non"),
        token=_token(params))


def op_shared_default_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "defaults": _get().list_shared_default()}


def op_shared_default_set(params: dict) -> Dict[str, Any]:
    return _get("w").set_shared_default(
        params.get("type", ""),
        tag_type=params.get("tag_type", "*"),
        tag_value=params.get("tag_value", "*"),
        can_be_shared=params.get("can_be_shared", "non"),
        token=_token(params))


# ── namespace ────────────────────────────────────────────────────────────

def op_ns_create(params: dict) -> Dict[str, Any]:
    return _get("w").create_namespace(params.get("ns", ""),
                                      parent=params.get("parent"),
                                      description=params.get("description", ""),
                                      token=_token(params))


def op_ns_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "namespaces": _get().list_namespaces(
        parent=params.get("parent"))}


def op_ns_list_all(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "namespaces": _get().list_namespaces_recursive(
        prefix=params.get("prefix", ""))}


def op_ns_get(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "namespace": _get().get_namespace(params.get("ns", ""))}


def op_ns_delete(params: dict) -> Dict[str, Any]:
    return _get("w").delete_namespace(params.get("ns", ""), token=_token(params))


# ── path ─────────────────────────────────────────────────────────────────

def op_path_create(params: dict) -> Dict[str, Any]:
    return _get("w").create_path(params.get("path_name", ""),
                                 params.get("address", ""),
                                 scheme=params.get("scheme", "file"),
                                 description=params.get("description", ""),
                                 token=_token(params))


def op_path_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "paths": _get().list_paths(
        scheme=params.get("scheme", ""))}


def op_path_resolve(params: dict) -> Dict[str, Any]:
    return {"status": "ok", **_get().resolve_path(params.get("target", ""))}


# ── buffer ───────────────────────────────────────────────────────────────

def op_buffer_push(params: dict) -> Dict[str, Any]:
    return _get("w").buffer_push(params.get("ops", []),
                                 external_tag=params.get("external_tag", ""),
                                 token=_token(params))


def op_buffer_process(params: dict) -> Dict[str, Any]:
    return _get("w").buffer_process(token=_token(params),
                                    limit=int(params.get("limit", 500) or 500))


def op_buffer_status(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "ops": _get().buffer_status(
        external_tag=params.get("external_tag", ""),
        status=params.get("status", ""))}


def op_buffer_retry(params: dict) -> Dict[str, Any]:
    return _get("w").buffer_retry(token=_token(params),
                                  limit=int(params.get("limit", 500) or 500))


# ── privileges (famille SECURITY, writer privé) ─────────────────────────

def op_priv_create(params: dict) -> Dict[str, Any]:
    """Crée une autorisation (priv_token requis)."""
    try:
        return _get("w", priv=True).create_privilege(
            params.get("chemin_ref", ""), kind=params.get("kind", "path"),
            agent_id=int(params.get("agent_id", -1) or -1),
            team=int(params.get("team", -1) or -1),
            level=int(params.get("level", 1) or 1),
            read=params.get("read", "----"), write=params.get("write", "----"),
            exec=params.get("exec", "----"), privileged=params.get("privileged", "----"),
            ask=params.get("ask", "none"), deadline=params.get("deadline"),
            description=params.get("description", ""),
            token=_priv_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_priv_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "privileges": _get().list_privileges(
        kind=params.get("kind", ""))}


def op_priv_resolve(params: dict) -> Dict[str, Any]:
    rows = _get().resolve_privilege(
        params.get("chemin", ""), kind=params.get("kind", "path"),
        agent_id=int(params.get("agent_id", -1) or -1),
        team=int(params.get("team", -1) or -1))
    return {"status": "ok", "privileges": rows}


def op_priv_check(params: dict) -> Dict[str, Any]:
    """Vérifie SANS consommer (route read)."""
    return _get().check_privilege_dict(
        params.get("chemin", ""), level=params.get("level", "agent"),
        op=params.get("op", "read"),
        agent_id=int(params.get("agent_id", -1) or -1),
        team=int(params.get("team", -1) or -1))


def op_priv_use(params: dict) -> Dict[str, Any]:
    """Vérifie + CONSOMME (nb_times/lastcall) — writer privé."""
    return _get("w", priv=True).use_privilege(
        params.get("chemin", ""), level=params.get("level", "agent"),
        op=params.get("op", "read"),
        agent_id=int(params.get("agent_id", -1) or -1),
        team=int(params.get("team", -1) or -1),
        kind=params.get("kind", "path"),
        token=_priv_token(params))


def op_priv_approve(params: dict) -> Dict[str, Any]:
    return {"status": "ok", **_get("w", priv=True).approve_privilege(
        params.get("decider_agent_id", 0), params.get("decider_level", "agent"),
        chemin=params.get("chemin", ""), kind=params.get("kind", "path"),
        token=_priv_token(params))}


# ── compat batch (importeurs V1) ─────────────────────────────────────────

def op_batch_status(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "batch": _get().batch_status(params.get("batch_id", ""))}


# ── enregistrement des routes ────────────────────────────────────────────

register("catalogue_local/data_types/list", op_data_types_list)
register("catalogue_local/data_types/get", op_data_types_get)
register("catalogue_local/data_types/add", op_data_types_add)
register("catalogue_local/data_types/modify", op_data_types_modify)
register("catalogue_local/data_types/delete", op_data_types_delete)

register("catalogue_local/data/add", op_data_add)
register("catalogue_local/data/modify", op_data_modify)
register("catalogue_local/data/delete", op_data_delete)
register("catalogue_local/data/get", op_data_get)
register("catalogue_local/data/list", op_data_list)
register("catalogue_local/data/list_recursive", op_data_list_recursive)
register("catalogue_local/data/search", op_data_search)
register("catalogue_local/data/refresh", op_data_refresh)
register("catalogue_local/row/add_column", op_row_add_column)

register("catalogue_local/tag_type/list", op_tag_type_list)
register("catalogue_local/tag_type/add", op_tag_type_add)
register("catalogue_local/tag_type/modify", op_tag_type_modify)
register("catalogue_local/tag_type/delete", op_tag_type_delete)
register("catalogue_local/tag/attach", op_tag_attach)
register("catalogue_local/tag/detach", op_tag_detach)
register("catalogue_local/tag/list", op_tag_list)
register("catalogue_local/tag/by_value", op_tag_by_value)

register("catalogue_local/sharing/get", op_sharing_get)
register("catalogue_local/sharing/set", op_sharing_set)
register("catalogue_local/shared_default/list", op_shared_default_list)
register("catalogue_local/shared_default/set", op_shared_default_set)

register("catalogue_local/namespace/create", op_ns_create)
register("catalogue_local/namespace/list", op_ns_list)
register("catalogue_local/namespace/list_all", op_ns_list_all)
register("catalogue_local/namespace/get", op_ns_get)
register("catalogue_local/namespace/delete", op_ns_delete)

register("catalogue_local/path/create", op_path_create)
register("catalogue_local/path/list", op_path_list)
register("catalogue_local/path/resolve", op_path_resolve)

register("catalogue_local/buffer/push", op_buffer_push)
register("catalogue_local/buffer/process", op_buffer_process)
register("catalogue_local/buffer/status", op_buffer_status)
register("catalogue_local/buffer/retry", op_buffer_retry)

register("catalogue_local/priv/create", op_priv_create)
register("catalogue_local/priv/list", op_priv_list)
register("catalogue_local/priv/resolve", op_priv_resolve)
register("catalogue_local/priv/check", op_priv_check)
register("catalogue_local/priv/use", op_priv_use)
register("catalogue_local/priv/approve", op_priv_approve)

register("catalogue_local/batch/status", op_batch_status)