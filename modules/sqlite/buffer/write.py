"""write — écritures du domaine buffer (dépôt + status).

Buffer = tampon des opérations vers/des le catalogue local. VOCABULAIRE :

- import_ops      (ailleurs → buffer)     : dépôt pending direction='in'
                                           (importeur externe).
- import_included : variante STRICTE d'import_ops — None ⟺ la donnée
                    porte elle-même source/version (requis, sinon erreur) ;
                    une valeur = la donnée NE la porte pas → appliquée à
                    toutes les ops.
- import_local    (buffer → local)        : le consumer, vit dans le
                                           domaine local (local/write).
- export_local    (local → buffer)        : dépôt pending direction='out'
                                           (modifs locales à exporter).
- export          (buffer → ailleurs)     : le consumer OUT — lit les ops
                                           'out' à envoyer. source/version
                                           sont des ARGUMENTS DE SÉLECTION :
                                           on n'envoie QUE les
                                           correspondances ; les payloads
                                           exportés gardent le quadruple
                                           (source/version dans les data).
- export_included : variante d'export — None = aucune contrainte de
                    source/version (tout exporter).
- mark_status / purge_applied : marquage des status et nettoyage. Le
  consumer du buffer (import_local) vit dans le domaine local : c'est le
  writer LOCAL qui applique les ops dans x_data, et qui est
  exceptionnellement autorisé à marquer/supprimer ici — via CES fonctions,
  jamais d'écriture brute sur buffer.db.
- retry : repasse des ops en pending (le re-traitement est fait par
  import_local).

Chaque appel exige le token du writer buffer (write_buffer) ; si token vaut
"" et que l'instance est un writer légitime (get_writer), le token lié de
l'instance est utilisé."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db, WriteDenied


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def require_writer(db: Db, token: str = "") -> None:
    """Garde d'écriture : token explicite OU token lié de l'instance."""
    db.check_write(token or db._write_token)


def resolve_version(payload: Dict[str, Any], version: str = "",
                    timestamp_field: str = "last_updated") -> str:
    """Version d'une op (règle writer buffer) :

    1. version explicite de l'op (payload['version']) ;
    2. version argument de l'import ;
    3. TIMESTAMP DE LA SOURCE : payload['timestamp'] ou
       payload['value'][timestamp_field] (les données d'origine portent
       leur fraîcheur — ex. last_updated de models.dev) ;
    4. sinon : date du jour (ISO) = version du snapshot.

    La règle garantit que chaque ligne du catalogue porte une version, et
    que le re-téléversement d'un snapshot SANS changement de la source
    retombe sur la MÊME version (upsert en place, pas de doublons)."""
    v = (payload.get("version") or "").strip() or version.strip()
    if v:
        return v
    ts = payload.get("timestamp")
    if not ts:
        value = payload.get("value")
        if isinstance(value, dict):
            ts = value.get(timestamp_field)
    if ts:
        return str(ts).strip()
    return date.today().isoformat()


def _import_rows(ops: List[Dict[str, Any]], external_tag: str,
                 source: Optional[str], version: Optional[str],
                 timestamp_field: str, strict: bool) -> List[Dict[str, Any]]:
    """Construit les lignes buffer_op (direction='in') pour `ops`.

    strict=False (import_ops) : `source`/`version` = DÉFAUTS ; les payloads
    qui les portent font foi ; version résolue par resolve_version (jamais
    d'op sans version).
    strict=True  (import_included) : None ⟺ la donnée doit la porter
    (sinon ValueError) ; une valeur est appliquée à toutes les ops."""
    rows = []
    for o in ops:
        payload = dict(o.get("payload") or {})
        if strict:
            if source is not None:
                payload["source"] = source
            elif not (payload.get("source") or "").strip():
                raise ValueError(
                    "import_included : source non fournie ET absente du "
                    "payload — passez-la en argument ou dans la donnée")
            if version is not None:
                payload["version"] = version
            else:
                v = (payload.get("version") or "").strip()
                if not v:
                    ts = payload.get("timestamp")
                    if not ts:
                        value = payload.get("value")
                        if isinstance(value, dict):
                            ts = value.get(timestamp_field)
                    v = str(ts).strip() if ts else ""
                if not v:
                    raise ValueError(
                        "import_included : version incluse absente du "
                        "payload (payload['version'] ou timestamp requis)")
                payload["version"] = v
        else:
            src = (payload.get("source") or "").strip() or source.strip()
            if not src:
                raise ValueError("op sans source (payload['source'] ou arg "
                                 "`source` requis)")
            payload["source"] = src
            payload["version"] = resolve_version(payload, version or "",
                                                 timestamp_field)
        rows.append({"direction": "in", "domain": o.get("domain", ""),
                     "op": o.get("op", "add"),
                     "payload_json": json.dumps(payload),
                     "external_tag": external_tag,
                     "ref_external": o.get("ref_external", "")})
    return rows


def import_ops(db: Db, ops: List[Dict[str, Any]], external_tag: str = "",
               source: str = "", version: str = "",
               timestamp_field: str = "last_updated",
               token: str = "") -> Dict[str, Any]:
    """import (ailleurs → buffer) : dépose N ops 'pending' — UNE écriture
    batch.

    op : {"domain", "op": add|modify|delete, "payload", ...}.
    Le payload doit porter le QUADRUPLE (namespace, name, source, version)
    de la data cible — c'est la source de l'op qui détermine les lignes
    qu'il peut toucher (jamais celles d'une autre source).

    `source`/`version` : SANS OBJET si le payload les porte ; sinon :
    - source  : la source de l'op (requise — sinon ValueError) ;
    - version : résolue par resolve_version (timestamp de la source, puis
      date du jour)."""
    require_writer(db, token)
    token = token or db._write_token
    if not ops:
        return {"ok": True, "pushed": 0}
    rows = _import_rows(ops, external_tag, source, version,
                        timestamp_field, strict=False)
    db.table("buffer_op").add(rows, token=token)
    return {"ok": True, "pushed": len(ops)}


def import_included(db: Db, ops: List[Dict[str, Any]],
                    external_tag: str = "", source: Optional[str] = None,
                    version: Optional[str] = None,
                    timestamp_field: str = "last_updated",
                    token: str = "") -> Dict[str, Any]:
    """import_included : variante STRICTE d'import_ops.

    Convention : passer None ⟺ la donnée porte elle-même source/version
    (obligatoire — sinon ValueError) ; passer une valeur ⟺ la donnée ne la
    porte pas → appliquée à toutes les ops. Pas de fallback date du jour en
    mode strict (version None = payload['version'] ou timestamp requis)."""
    require_writer(db, token)
    token = token or db._write_token
    if not ops:
        return {"ok": True, "pushed": 0}
    rows = _import_rows(ops, external_tag, source, version,
                        timestamp_field, strict=True)
    db.table("buffer_op").add(rows, token=token)
    return {"ok": True, "pushed": len(ops)}


def export_local(db: Db, ops: List[Dict[str, Any]], external_tag: str = "",
                 token: str = "") -> Dict[str, Any]:
    """export_local (local → buffer) : dépose des ops direction='out'
    (modifs locales à exporter — consumer OUT à venir). Seul le writer
    buffer écrit ; le writer local passe PAR cette fonction (autorisation
    spéciale), jamais en brut sur buffer.db."""
    require_writer(db, token)
    token = token or db._write_token
    if not ops:
        return {"ok": True, "pushed": 0}
    rows = [{"direction": "out", "domain": o.get("domain", ""),
             "op": o.get("op", "add"),
             "payload_json": json.dumps(o.get("payload", {})),
             "external_tag": external_tag,
             "ref_external": o.get("ref_external", "")} for o in ops]
    db.table("buffer_op").add(rows, token=token)
    return {"ok": True, "pushed": len(ops)}


def _select_out(db: Db, token: str, domain: str, source: str, version: str,
                external_tag: str, limit: Optional[int]) -> List[Dict[str, Any]]:
    """Ops direction='out' pending, filtrées par ARGUMENTS (on n'envoie que
    les sources/versions correspondantes). Les payloads conservent le
    quadruple (la source/version restent dans les data exportées)."""
    where: Dict[str, Any] = {"direction": "out", "status": "pending"}
    if domain:
        where["domain"] = domain
    if external_tag:
        where["external_tag"] = external_tag
    rows = db.table("buffer_op").select(where=where,
                                        order_by="op_id",
                                        limit=limit)
    out = []
    for r in rows:
        try:
            payload = json.loads(r["payload_json"])
        except (TypeError, ValueError):
            continue
        if source and payload.get("source") != source:
            continue
        if version and payload.get("version") != version:
            continue
        out.append({"op_id": r["op_id"], "domain": r["domain"],
                    "op": r["op"], "payload": payload,
                    "external_tag": r["external_tag"],
                    "ref_external": r["ref_external"]})
    return out


def export(db: Db, domain: str = "", source: str = "", version: str = "",
           external_tag: str = "", limit: Optional[int] = None,
           token: str = "") -> Dict[str, Any]:
    """export (buffer → ailleurs) : consumer OUT — les ops direction='out'
    pending à envoyer. `source`/`version` = ARGUMENTS DE SÉLECTION : seules
    les ops correspondantes sont renvoyées ("" = toutes).

    Les ops restent 'pending' tant que le sender n'a pas confirmé :
    mark_status(op_ids, 'applied') après envoi réussi (ou 'error')."""
    require_writer(db, token)
    token = token or db._write_token
    ops = _select_out(db, token, domain, source, version, external_tag,
                      limit)
    return {"ok": True, "exported": len(ops), "ops": ops}


def export_included(db: Db, domain: str = "", source: Optional[str] = None,
                    version: Optional[str] = None, external_tag: str = "",
                    limit: Optional[int] = None,
                    token: str = "") -> Dict[str, Any]:
    """export_included : variante d'export — None = aucune contrainte de
    source/version (tout exporter). Les payloads exportés gardent la
    source/version INCLUSES dans les data (quadruple complet)."""
    require_writer(db, token)
    token = token or db._write_token
    ops = _select_out(db, token, domain, source or "", version or "",
                      external_tag, limit)
    return {"ok": True, "exported": len(ops), "ops": ops}


def mark_status(db: Db, op_ids: List[int], status: str, error: str = "",
                token: str = "") -> int:
    """Marque des ops (applied/error/cancelled). Appelé par le consumer du
    domaine local (import_local — autorisation spéciale du writer local)
    et par le sender OUT après envoi."""
    require_writer(db, token)
    token = token or db._write_token
    if not op_ids:
        return 0
    tbl = db.table("buffer_op")
    n = 0
    for oid in op_ids:
        changes: Dict[str, Any] = {"status": status}
        if error:
            changes["error"] = error
        if status == "applied":
            changes["applied_at"] = _now()
        n += tbl.update({"op_id": oid}, changes, token=token)
    return n


def purge_applied(db: Db, before: Optional[str] = None, token: str = "") -> int:
    """« J'ai fini » : supprime les ops applied (audit court), éventuellement
    seulement celles antérieures à `before` (ISO). Appelé par le consumer du
    domaine local à la fin d'un cycle."""
    require_writer(db, token)
    token = token or db._write_token
    if before:
        return db.table("buffer_op").sql(
            "DELETE FROM buffer_op WHERE status='applied' AND applied_at < ?",
            (before,), token=token)
    return db.table("buffer_op").remove({"status": "applied"}, token=token)


def retry(db: Db, op_ids: List[int] = (), token: str = "") -> Dict[str, Any]:
    """Repasse des ops en pending pour re-traitement (error → pending).
    Sans op_ids : toutes les erreurs. Le retraitement est fait par
    import_local (domaine local)."""
    require_writer(db, token)
    token = token or db._write_token
    tbl = db.table("buffer_op")
    if op_ids:
        for oid in op_ids:
            tbl.update({"op_id": oid}, {"status": "pending", "error": ""},
                       token=token)
    else:
        tbl.sql("UPDATE buffer_op SET status='pending', error='' "
                "WHERE status='error'", token=token)
    return {"ok": True, "pending": tbl.count({"status": "pending"})}
