"""Routes catalogue_local/* — domaine du catalogue local.

Architecture :
  - LECTURES directes pour tout le monde (connexion mode=ro, zéro write).
  - ÉCRITURES DONNÉES : exigent le token du writer catalogue (write_catalogue)
    — garde app + mode=ro (garde moteur). Le seul writer autorisé sur les
    données est write_catalogue.
  - ÉCRITURES AUTORISATIONS : exigent le token du writer PRIVÉ
    (write_catalogue_priv, env MW_CATALOGUE_PRIV_WRITE_TOKEN) — distinct du
    writer catalogue. La table privileges/conditions n'est accessible en
    écriture QU'à ce writer privé (consommation nb_times incluse).
  - Types de données créés à chaud (hot-add), namespaces imbriqués,
    tags/limites/source_and_sharing par type, last_access bufferisé.

Routes (lecture) :
  catalogue_local/list_catalogues
  catalogue_local/list                  {type, namespace, page, sort, order, status}
  catalogue_local/list_recursive        {type, namespace}
  catalogue_local/get                   {type, ref}
  catalogue_local/search                {type, q, tag_type, tag_value}
  catalogue_local/namespaces/list       {parent}
  catalogue_local/namespaces/list_all   {prefix}
  catalogue_local/namespaces/get        {ns}
  catalogue_local/tags/list             {type, ref}
  catalogue_local/tags/types            {type}
  catalogue_local/tags/by_value         {type, tag_type, tag_value}
  catalogue_local/limites/list          {type, ref}
  catalogue_local/sharing/get           {type, ref}
  catalogue_local/sharing/resolve       {type, ref}
  catalogue_local/shared_default/list
  catalogue_local/path/list
  catalogue_local/path/resolve
  catalogue_local/value/load
  catalogue_local/priv/list             (autorisations, lecture)
  catalogue_local/priv/resolve          (autorisations, lecture)
  catalogue_local/priv/check            (autorisations, NE consomme PAS)

Routes (écriture données, token write_catalogue) :
  catalogue_local/write/create_type     {token, type, description}
  catalogue_local/write/upsert          {token, type, ref, name, namespace,
                                         version, value, description, status}
  catalogue_local/write/delete          {token, type, ref}
  catalogue_local/write/create_namespace {token, ns, parent, description}
  catalogue_local/write/tag_type        {token, type, tag_type, description}
  catalogue_local/write/tag_attach      {token, type, ref, tag_type, tag_value}
  catalogue_local/write/tag_detach      {token, type, ref, tag_type, tag_value}
  catalogue_local/write/limite_set      {token, type, ref, limite, valeur}
  catalogue_local/write/sharing_set     {token, type, ref, source, source_url,
                                         is_it_shared, can_be_shared}
  catalogue_local/write/shared_default  {token, data_type, tag_type, tag_value,
                                         can_be_shared}
  catalogue_local/write/path_create     {token, path_name, address, scheme}
  catalogue_local/write/flush_access    {token}

Routes (écriture autorisations, token PRIVÉ) :
  catalogue_local/write/priv_create     {priv_token, chemin_ref, kind, level,
                                         read, write, exec, privileged,
                                         agent_id, team, deadline, conditions}
  catalogue_local/priv/use              {priv_token, chemin, level, op, kind,
                                         agent_id, team} → vérifie + CONSOMME
"""

import json
import os
from typing import Any, Dict

from services.api.router import register

from modules.sql.catalogue_local import (
    CatalogueTypeNotFound, SHARING_LEVELS, SOURCE_VALUES,
)

# Token du writer catalogue (données). En prod, injecté par le service ; ici
# fallback env MW_CATALOGUE_WRITE_TOKEN. Les lecteurs ne connaissent pas ce
# token et n'ont pas de route write → refus.
WRITE_TOKEN = os.environ.get("MW_CATALOGUE_WRITE_TOKEN", "write_catalogue")

# Token du writer PRIVÉ (autorisations privileges/conditions). Distinct du
# writer catalogue : seul lui peut créer/consommer des autorisations.
PRIV_WRITE_TOKEN = os.environ.get("MW_CATALOGUE_PRIV_WRITE_TOKEN",
                                  "write_catalogue_priv")


def _get(mode: str = "ro", priv: bool = False):
    """Retourne l'instance LocalCatalogue.

    mode=ro (défaut) : connexion lecture seule. mode=w + priv=False : writer
    catalogue (données). mode=w + priv=True : writer PRIVÉ (autorisations)."""
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


# ── Lecture ─────────────────────────────────────────────────────────────

def op_list_catalogues(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "catalogues": _get().list_catalogues()}


def op_list(params: dict) -> Dict[str, Any]:
    try:
        res = _get().list(
            params.get("type", ""),
            namespace=params.get("namespace", ""),
            page=params.get("page", 1), page_size=params.get("page_size", 100),
            sort=params.get("sort", "name"), order=params.get("order", "asc"),
            status=params.get("status", ""),
        )
        return {"status": "ok", **res}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_list_recursive(params: dict) -> Dict[str, Any]:
    try:
        items = _get().list_recursive(
            params.get("type", ""),
            namespace=params.get("namespace", ""),
            status=params.get("status", ""),
        )
        return {"status": "ok", "items": items}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_get(params: dict) -> Dict[str, Any]:
    try:
        return {"status": "ok", "entry": _get().get(params.get("type", ""),
                                                    params.get("ref", ""))}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_search(params: dict) -> Dict[str, Any]:
    try:
        items = _get().search(
            params.get("type", ""), q=params.get("q", ""),
            tag_type=params.get("tag_type", ""),
            tag_value=params.get("tag_value", ""),
            limit=params.get("limit", 50),
        )
        return {"status": "ok", "items": items}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_ns_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok",
            "namespaces": _get().list_namespaces(parent=params.get("parent"))}


def op_ns_list_all(params: dict) -> Dict[str, Any]:
    return {"status": "ok",
            "namespaces": _get().list_namespaces_recursive(prefix=params.get("prefix", ""))}


def op_ns_get(params: dict) -> Dict[str, Any]:
    ns = _get().get_namespace(params.get("ns", ""))
    return {"status": "ok" if ns else "not_found", "namespace": ns}


def op_tags_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "tags": _get().list_tags(params.get("type", ""),
                                                     params.get("ref", ""))}


def op_tags_types(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "tag_types": _get().list_tag_types(params.get("type", ""))}


def op_tags_by_value(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "items": _get().tags_by_value(
        params.get("type", ""), params.get("tag_type", ""),
        params.get("tag_value", ""))}


def op_limites_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "limites": _get().list_limites(
        params.get("type", ""), params.get("ref", ""))}


def op_sharing_get(params: dict) -> Dict[str, Any]:
    s = _get().get_sharing(params.get("type", ""), params.get("ref", ""))
    return {"status": "ok" if s else "not_found", "sharing": s}


def op_sharing_resolve(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "can_be_shared": _get().resolve_can_be_shared(
        params.get("type", ""), params.get("ref", ""))}


def op_shared_default_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "defaults": _get().list_shared_default()}


# ── Écriture (writer uniquement) ────────────────────────────────────────

def op_write_create_type(params: dict) -> Dict[str, Any]:
    try:
        return _get("w").create_type(params.get("type", ""),
                                     params.get("description", ""),
                                     token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_upsert(params: dict) -> Dict[str, Any]:
    """Crée ou met à jour une entrée (writer uniquement)."""
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        table = db._cat_table(type_)
        ref = params.get("ref", "")
        # Espace réservé : auto-test-* autorisé (tests), sinon interdit.
        db._check_reserved(ref)
        ns = params.get("namespace", "")
        if ns:
            db._check_reserved(ns)
        entry = db._entry(type_, ref)
        now = "datetime('now')"
        # value/ref_file/path : value peut être un gros string (yaml/json/py)
        # ou vide ; si vide + ref_file (adresse) ou path (symbolique) → le get
        # résoudra le contenu depuis le fichier.
        value = params.get("value", "")
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False)
        if entry is None:
            db.conn.execute(
                f"INSERT INTO {table} (ref, name, namespace, version, value, "
                "ref_file, path, description, data_type, status) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (ref, params.get("name", ref.split("@")[0].split("/")[-1]),
                 params.get("namespace", ""),
                 params.get("version", "latest"),
                 str(value),
                 params.get("ref_file", ""),
                 params.get("path", ""),
                 params.get("description", ""), type_,
                 params.get("status", "active")))
        else:
            sets = ["name = ?", "value = ?", "description = ?",
                    "status = ?", "updated_at = ?"]
            vals = [params.get("name", entry["name"]),
                    str(value) if "value" in params
                    else json.dumps(json.loads(entry["value"] or "{}")),
                    params.get("description", entry["description"] or ""),
                    params.get("status", entry["status"]), now]
            if "namespace" in params:
                sets.append("namespace = ?"); vals.append(params["namespace"])
            if "version" in params:
                sets.append("version = ?"); vals.append(params["version"])
            if "ref_file" in params:
                sets.append("ref_file = ?"); vals.append(params["ref_file"])
            if "path" in params:
                sets.append("path = ?"); vals.append(params["path"])
            db.conn.execute(
                f"UPDATE {table} SET {', '.join(sets)} WHERE id = ?",
                vals + [entry["id"]])
        db._bump_modify(type_, ref)
        db.conn.commit()
        return {"status": "ok", "type": type_, "ref": ref}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_delete(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        entry = db._entry(type_, params.get("ref", ""))
        if entry is None:
            return {"status": "not_found"}
        db.conn.execute(f"DELETE FROM {type_}_catalogue WHERE id = ?", (entry["id"],))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_create_namespace(params: dict) -> Dict[str, Any]:
    try:
        return _get("w").create_namespace(
            params.get("ns", ""), params.get("parent"),
            params.get("description", ""), token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def _require_entry(db, type_: str, ref: str):
    entry = db._entry(type_, ref)
    if entry is None:
        raise CatalogueTypeNotFound(f"{type_}/{ref} introuvable")
    return entry


def op_write_tag_type(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        db._cat_table(type_)  # vérifie l'existence
        db.conn.execute(
            f"INSERT OR IGNORE INTO {type_}_tag_types(tag_type, description) "
            "VALUES (?, ?)", (params.get("tag_type", ""),
                             params.get("description", "")))
        db.conn.commit()
        return {"status": "ok", "tag_type": params.get("tag_type", "")}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_tag_attach(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        entry = _require_entry(db, type_, params.get("ref", ""))
        db.conn.execute(
            f"INSERT OR IGNORE INTO {type_}_tags(entry_id, tag_type, tag_value) "
            "VALUES (?, ?, ?)",
            (entry["id"], params.get("tag_type", ""), params.get("tag_value", "")))
        db._bump_modify(type_, params.get("ref", ""))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_tag_detach(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        entry = _require_entry(db, type_, params.get("ref", ""))
        db.conn.execute(
            f"DELETE FROM {type_}_tags WHERE entry_id = ? AND tag_type = ? "
            "AND tag_value = ?",
            (entry["id"], params.get("tag_type", ""), params.get("tag_value", "")))
        db._bump_modify(type_, params.get("ref", ""))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_limite_set(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        entry = _require_entry(db, type_, params.get("ref", ""))
        db.conn.execute(
            f"INSERT INTO {type_}_limites(entry_id, limite, valeur_json) "
            "VALUES (?, ?, ?) ON CONFLICT(entry_id, limite) DO UPDATE SET "
            "valeur_json = excluded.valeur_json",
            (entry["id"], params.get("limite", ""),
             json.dumps(params.get("valeur", {}))))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_sharing_set(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        type_ = params.get("type", "")
        entry = _require_entry(db, type_, params.get("ref", ""))
        can = params.get("can_be_shared", "non")
        if can not in SHARING_LEVELS:
            return {"status": "error", "error": f"can_be_shared invalide: {can}"}
        source = params.get("source", "perso")
        if source not in SOURCE_VALUES:
            return {"status": "error", "error": f"source invalide: {source}"}
        db.conn.execute(
            f"INSERT INTO {type_}_source_and_sharing "
            "(entry_id, source, source_url, is_from_share, is_it_shared, can_be_shared, shared_at) "
            "VALUES (?, ?, ?, ?, ?, ?, datetime('now')) "
            f"ON CONFLICT(entry_id) DO UPDATE SET "
            "source = excluded.source, source_url = excluded.source_url, "
            "is_from_share = excluded.is_from_share, "
            "is_it_shared = excluded.is_it_shared, "
            "can_be_shared = excluded.can_be_shared, "
            "shared_at = excluded.shared_at",
            (entry["id"], source, params.get("source_url", ""),
             1 if params.get("is_from_share") else 0,
             1 if params.get("is_it_shared") else 0, can))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_shared_default(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        can = params.get("can_be_shared", "non")
        if can not in SHARING_LEVELS:
            return {"status": "error", "error": f"can_be_shared invalide: {can}"}
        db.conn.execute(
            "INSERT INTO shared_default(data_type, tag_type, tag_value, can_be_shared) "
            "VALUES (?, ?, ?, ?) "
            "ON CONFLICT(data_type, tag_type, tag_value) DO UPDATE SET "
            "can_be_shared = excluded.can_be_shared",
            (params.get("data_type", ""), params.get("tag_type", "*"),
             params.get("tag_value", "*"), can))
        db.conn.commit()
        return {"status": "ok"}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_flush_access(params: dict) -> Dict[str, Any]:
    try:
        db = _get("w")
        db._check_write(_token(params))
        n = db.flush_access()
        return {"status": "ok", "flushed": n}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


# ── catalogue_path : environnement de paths local ─────────────────────────

def op_path_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok", "paths": _get().list_paths(scheme=params.get("scheme", ""))}


def op_path_resolve(params: dict) -> Dict[str, Any]:
    return _get().resolve_path(params.get("path", ""))


def op_path_create(params: dict) -> Dict[str, Any]:
    """Déclare un path symbolique → adresse (writer seul). `*` interdit."""
    try:
        return _get("w").create_path(
            params.get("path_name", ""), params.get("address", ""),
            scheme=params.get("scheme", "file"),
            description=params.get("description", ""),
            token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_value_load(params: dict) -> Dict[str, Any]:
    """Charge le contenu d'une entrée (résout value depuis ref_file/path)."""
    try:
        db = _get()
        entry = db._entry(params.get("type", ""), params.get("ref", ""))
        if entry is None:
            return {"status": "not_found"}
        db._resolve_value(entry)
        return {"status": "ok", "value": entry.get("value", ""),
                "source": entry.get("_value_source", "")}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


# ── privileges : autorisations par chemin ─────────────────────────────────

def op_priv_list(params: dict) -> Dict[str, Any]:
    return {"status": "ok",
            "privileges": _get().list_privileges(kind=params.get("kind", ""))}


def op_priv_resolve(params: dict) -> Dict[str, Any]:
    try:
        return _get().resolve_privilege(
            params.get("chemin", ""), kind=params.get("kind", ""),
            agent_id=int(params.get("agent_id", -1)),
            team=int(params.get("team", -1)))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_priv_check(params: dict) -> Dict[str, Any]:
    """Vérifie un accès : {chemin, level, op, agent_id, team} → {allowed}.
    NE CONSOMME PAS (lecture)."""
    try:
        allowed = _get().check_privilege(
            params.get("chemin", ""), level=params.get("level", "agent"),
            op=params.get("op", "read"), kind=params.get("kind", ""),
            agent_id=int(params.get("agent_id", -1)),
            team=int(params.get("team", -1)))
        return {"status": "ok", "allowed": allowed}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_priv_use(params: dict) -> Dict[str, Any]:
    """(writer PRIVÉ) Usage réel : vérifie ET consomme nb_times.

    Flux : check → si autorisation numérotée (nb_times) → demande au writer
    de DÉCRÉMENTER → si compteur épuisé après décrément → demande de
    RENOUVELLEMENT signalée (renewal_request).

    {priv_token, chemin, level, op, kind, agent_id, team} → {allowed,
    consumed, remaining, renewal_request}."""
    try:
        return _get("w", priv=True).use_privilege(
            params.get("chemin", ""), level=params.get("level", "agent"),
            op=params.get("op", "read"), kind=params.get("kind", ""),
            agent_id=int(params.get("agent_id", -1)),
            team=int(params.get("team", -1)),
            token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_priv_approve(params: dict) -> Dict[str, Any]:
    """(writer PRIVÉ) Un décideur approuve une autorisation pour un tiers.

    {priv_token, decider_agent_id, decider_level, beneficiary_agent_id,
     beneficiary_level, chemin_ref, kind, read, write, exec, privileged,
     deadline, conditions, team}
    → {id_auth, read, write, exec, privileged}.
    Borné par la matrice de délégation + intersection décideur∩demande."""
    try:
        return _get("w", priv=True).approve_privilege(
            decider_agent_id=int(params.get("decider_agent_id", -1)),
            decider_level=params.get("decider_level", "agent"),
            beneficiary_agent_id=int(params.get("beneficiary_agent_id", -1)),
            beneficiary_level=params.get("beneficiary_level", "agent"),
            chemin_ref=params.get("chemin_ref", ""),
            kind=params.get("kind", "path"),
            read=params.get("read", ""), write=params.get("write", ""),
            exec_=params.get("exec", ""),
            privileged=params.get("privileged", ""),
            deadline=params.get("deadline", ""),
            conditions=params.get("conditions"),
            team=int(params.get("team", -1)),
            token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_priv_create(params: dict) -> Dict[str, Any]:
    """Déclare une règle d'autorisation (writer PRIVÉ uniquement).

    agent_id/team : -1 (défaut) = tous. kind ∈ ref|path|cmd.
    deadline : date ISO d'expiration. conditions : [{type, valeur, compteur}].
    ask : niveau de DEMANDE requis — none|security_supervisor|human|human_root
          (SÉPARÉ du droit : une cmd peut être exec AUTORISÉE mais exiger
          ask=human à chaque usage)."""
    try:
        return _get("w", priv=True).create_privilege(
            params.get("chemin_ref", ""), kind=params.get("kind", "path"),
            level=params.get("level", 1),
            read=params.get("read", "----"), write=params.get("write", "----"),
            exec_=params.get("exec", "----"),
            privileged=params.get("privileged", "----"),
            description=params.get("description", ""),
            agent_id=int(params.get("agent_id", -1)),
            team=int(params.get("team", -1)),
            deadline=params.get("deadline", ""),
            conditions=params.get("conditions"),
            ask=params.get("ask", "none"),
            token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


# ── Batchs d'écriture ─────────────────────────────────────────────────────
# Un batch regroupe jusqu'à MAX_BATCH_OPS opérations SQL dans UN SEUL lock +
# UNE transaction. Garde-fous : nb max de lignes par batch (100) ET durée
# limite (5s par défaut). Si la durée est estimée courte, le blocage
# temporaire des lecteurs est acceptable (WAL).

MAX_BATCH_OPS = 100

def _batch_ops_count(ops: list) -> int:
    """Nb total de lignes SQL d'un batch (sql + sqls)."""
    n = 0
    for op in ops or []:
        if op.get("sql"):
            n += 1
        n += len(op.get("sqls", []) or [])
    return n


def op_write_batch(params: dict) -> Dict[str, Any]:
    """Soumet un batch d'écritures (writer).

    {token, ops: [{sql, params} | {sqls, params_list}], max_duration_s,
     queue: immediate|normal}
    → {batch_id}. Garde-fou : max 100 lignes SQL par batch.
    queue="immediate" : create/destruct auth (prioritaire).
    queue="normal" (défaut) : modify_use (par batchs, avec répit après)."""
    try:
        ops = params.get("ops") or []
        if _batch_ops_count(ops) > MAX_BATCH_OPS:
            return {"status": "error",
                    "error": f"batch trop grand (max {MAX_BATCH_OPS} lignes SQL)"}
        return _get("w").submit_batch(
            ops,
            max_duration_s=float(params.get("max_duration_s", 5.0)),
            token=_token(params),
            queue=params.get("queue", "normal"))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_batch_status(params: dict) -> Dict[str, Any]:
    """Statut d'un batch soumis : {batch_id} → {status, error}."""
    try:
        res = _get("w").batch_status(params.get("batch_id", ""))
        if res is None:
            return {"status": "not_found", "batch_id": params.get("batch_id", "")}
        return {"status": "ok", **res}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


def op_write_cleanup(params: dict) -> Dict[str, Any]:
    """Nettoie un préfixe de l'espace réservé auto-test (writer).

    {token, prefix} — prefix DOIT commencer par auto-test-check-official.
    Supprime les entrées + namespaces correspondants (cleanup trivial)."""
    try:
        return _get("w").cleanup_prefix(params.get("prefix", ""),
                                        token=_token(params))
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}


# ── Enregistrement ───────────────────────────────────────────────────────

register("catalogue_local/list_catalogues", op_list_catalogues)
register("catalogue_local/list", op_list)
register("catalogue_local/list_recursive", op_list_recursive)
register("catalogue_local/get", op_get)
register("catalogue_local/search", op_search)
register("catalogue_local/namespaces/list", op_ns_list)
register("catalogue_local/namespaces/list_all", op_ns_list_all)
register("catalogue_local/namespaces/get", op_ns_get)
register("catalogue_local/tags/list", op_tags_list)
register("catalogue_local/tags/types", op_tags_types)
register("catalogue_local/tags/by_value", op_tags_by_value)
register("catalogue_local/limites/list", op_limites_list)
register("catalogue_local/sharing/get", op_sharing_get)
register("catalogue_local/sharing/resolve", op_sharing_resolve)
register("catalogue_local/shared_default/list", op_shared_default_list)
register("catalogue_local/path/list", op_path_list)
register("catalogue_local/path/resolve", op_path_resolve)
register("catalogue_local/value/load", op_value_load)
register("catalogue_local/priv/list", op_priv_list)
register("catalogue_local/priv/resolve", op_priv_resolve)
register("catalogue_local/priv/check", op_priv_check)
register("catalogue_local/priv/use", op_priv_use)
register("catalogue_local/priv/approve", op_priv_approve)
register("catalogue_local/batch/status", op_batch_status)

register("catalogue_local/write/create_type", op_write_create_type)
register("catalogue_local/write/upsert", op_write_upsert)
register("catalogue_local/write/delete", op_write_delete)
register("catalogue_local/write/create_namespace", op_write_create_namespace)
register("catalogue_local/write/path_create", op_path_create)
register("catalogue_local/write/priv_create", op_priv_create)
register("catalogue_local/write/tag_type", op_write_tag_type)
register("catalogue_local/write/tag_attach", op_write_tag_attach)
register("catalogue_local/write/tag_detach", op_write_tag_detach)
register("catalogue_local/write/limite_set", op_write_limite_set)
register("catalogue_local/write/sharing_set", op_write_sharing_set)
register("catalogue_local/write/shared_default", op_write_shared_default)
register("catalogue_local/write/flush_access", op_write_flush_access)
register("catalogue_local/write/batch", op_write_batch)
register("catalogue_local/write/cleanup", op_write_cleanup)
register("catalogue_local/write/batch", op_write_batch)
