from __future__ import annotations

import json
import re
import time
from datetime import timedelta
from typing import Any, Dict, List, Optional

import xxhash

from modules.sqlite.base import Db

# ── Constantes du domaine (V2 figées) ─────────────────────────
MAX_PRIV_LEVEL = (1 << 32) - 1
ASK_LEVELS = ("none", "security_supervisor", "human", "human_root")
SHARING_LEVELS = ("non", "everyone", "enterprise", "friends", "official")
SOURCE_VALUES = ("perso", "distant", "enterprise", "github/depot", "official")
TAG_VALUE_TYPES = ("bool", "text", "number", "date", "list", "range")
SCALAR_VALUE_TYPES = ("string", "int", "uint", "float", "bool", "date",
                      "timestamp", "json")
EXTERNAL_VALUE_TYPES = ("file",)
DATA_STATUSES = ("active", "missing", "archived")
RESERVED_TEST_PREFIX = "auto-test-check-official"
RESERVED_SYSTEM_PREFIXES = ("auto/", "system/", "catalogue/")

_SEED = 0x5EEDC0DE
_TEXT_N = re.compile(r"^text\[\d+ch\]$")

BASE_COLUMNS = {
    "data_id": "INTEGER PRIMARY KEY",
    "ref": "TEXT UNIQUE NOT NULL",
    "name": "TEXT DEFAULT ''",
    "namespace": "TEXT DEFAULT ''",
    "version": "TEXT DEFAULT 'latest'",
    "data_value_type": "TEXT NOT NULL",
    "value": "TEXT DEFAULT '{}'",
    "ref_file": "TEXT DEFAULT ''",
    "path": "TEXT DEFAULT ''",
    "description": "TEXT DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "last_modify": "TEXT DEFAULT NULL",
    "created_at": "TEXT DEFAULT (datetime('now'))",
    "updated_at": "TEXT DEFAULT (datetime('now'))",
}

_TYPE_TO_SQL = {
    "text": "TEXT", "string": "TEXT", "date": "TEXT", "json": "TEXT",
    "file": "TEXT", "row": "TEXT",
    "int": "INTEGER", "uint": "INTEGER", "bool": "INTEGER",
    "timestamp": "INTEGER", "float": "REAL",
}
_TAG_TYPE_TO_SQL = {
    "bool": "INTEGER", "text": "TEXT", "number": "REAL",
    "date": "TEXT", "list": "TEXT", "range": "TEXT",
}


# ── Helpers identitaires / typage ─────────────────────────────
def data_id_of(ref: str) -> int:
    """Hash stable (int64 positif) de la ref — identique load/reload."""
    return xxhash.xxh64(str(ref), seed=_SEED).intdigest() & 0x7FFFFFFFFFFFFFFF


def parse_row_type(dvt: str) -> Optional[List[tuple]]:
    s = dvt.strip()
    if not (s.startswith("row(") and s.endswith(")")):
        return None
    inner = s[4:-1].strip()
    if not inner:
        return None
    out = []
    for part in inner.split(","):
        part = part.strip()
        if "=" not in part:
            raise ValueError(f"row invalide: {part!r} dans {dvt!r}")
        header, typ = part.split("=", 1)
        header, typ = header.strip(), typ.strip()
        if not header or not typ:
            raise ValueError(f"row invalide: {dvt!r}")
        out.append((header, typ))
    return out


def col_type_for(value_type: str) -> str:
    p = value_type.strip()
    if p in _TYPE_TO_SQL:
        return _TYPE_TO_SQL[p]
    if _TEXT_N.match(p):
        return "TEXT"
    raise ValueError(f"type de valeur inconnu: {value_type!r}")


def coerce(dvt: str, value: Any):
    d = dvt.strip()
    if d in ("json", "row", "file"):
        return value
    if d == "int":
        return int(value)
    if d == "uint":
        v = int(value)
        return v if v >= 0 else 0
    if d == "float":
        return float(value)
    if d == "bool":
        if isinstance(value, str):
            return 1 if value.lower() in ("1", "true", "yes", "on") else 0
        return 1 if value else 0
    if d == "timestamp":
        return int(value)
    if d == "date":
        return str(value)
    return str(value)


def is_reserved(ref: str) -> bool:
    return (ref.startswith(RESERVED_TEST_PREFIX)
            or any(ref.startswith(p) for p in RESERVED_SYSTEM_PREFIXES)
            or ref in ("",))


def now_iso() -> str:
    t = time.time()
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def now_shift(seconds: float) -> str:
    from datetime import datetime, timezone
    dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
    return dt.isoformat()


# ── Algorithmes / logique (pas de CRUD fin → read.py / write.py) ─

def resolve_path(db: Db, target: str) -> Dict[str, Any]:
    """Résolution par précision gauche→droite (/lib/a/$1 prime).

    Un segment `$N` du path_name CAPTURE le segment cible correspondant
    (joker) ; les segments littéraux doivent matcher exactement. L'adresse
    résolue = préfixe address + segments capturés ($) + segments en plus
    (tail). Contrairement à V2, le segment capturé est réellement réinjecté."""
    parts = target.split("/")
    rows = db.table("global_local_path").select(order_by="LENGTH(path_name) DESC")
    best, best_len, best_caps = None, -1, []
    for row in rows:
        pn = row["path_name"].split("/")
        if len(pn) > len(parts):
            continue
        score, ok, caps = 0, True, []
        for i, seg in enumerate(pn):
            if not seg or seg[0] == "$":
                if i < len(parts):
                    caps.append(parts[i])
                continue
            if seg != parts[i]:
                ok = False
                break
            score += 1
        if ok and score > best_len:
            best, best_len, best_caps = row, score, caps
    if not best:
        return {"resolved": False, "target": target, "address": target,
                "scheme": "file"}
    addr = best["address"].replace("/$", "")
    for seg in best_caps + parts[len(best["path_name"].split("/")):]:
        addr += "/" + seg
    return {"resolved": True, "target": target, "address": addr,
            "scheme": best["scheme"]}


def apply_op(db: Db, domain: str, op: str, payload: Dict[str, Any]) -> None:
    """Applique un op du buffer via la DataTable concernée."""
    if op == "delete":
        did = payload.get("data_id") or data_id_of(payload.get("ref", ""))
        db.table(f"{domain}_data").remove({"data_id": did})
        return
    from modules.sqlite.local.data_table import get_table
    dt = get_table(db, domain)
    dt.upsert(payload, token=db._write_token)


def buffer_process(db: Db, token: str = "", limit: int = 500) -> Dict[str, Any]:
    """Consumer du writer : applique les ops pending en mini-batchs
    (jamais bloquant — chaque op en try/except)."""
    db.check_write(token)
    tbl = db.table("global_local_buffer_op")
    pending = tbl.select(where={"status": "pending"}, order_by="op_id",
                         limit=limit)
    applied = errors = 0
    for row in pending:
        try:
            payload = json.loads(row["payload_json"] or "{}")
            apply_op(db, row["domain"], row["op"], payload)
            tbl.update({"op_id": row["op_id"]},
                       {"status": "applied", "applied_at": now_iso()}, token=token)
            applied += 1
        except Exception as e:
            tbl.update({"op_id": row["op_id"]},
                       {"status": "error", "error": str(e)[:500]}, token=token)
            errors += 1
    return {"ok": True, "applied": applied, "errors": errors,
            "pending_left": tbl.count({"status": "pending"})}


def _priv_matches(row: Dict[str, Any], chemin: str) -> bool:
    if row["kind"] == "cmd":
        target = (chemin or "").strip()
        declared = (row["chemin_ref"] or "").strip()
        return target == declared or (declared and target.startswith(declared + " "))
    if row["kind"] == "path":
        target = (chemin or "").strip("/")
        declared = (row["chemin_ref"] or "").strip("/")
        return target == declared or target.startswith(declared + "/")
    return chemin == row["chemin_ref"]


def resolve_privilege(db: Db, chemin: str, kind: str = "path",
                      agent_id: int = -1, team: int = -1) -> List[Dict[str, Any]]:
    """Match par précision (plus long chemin_ref en premier), filtrage
    d'identité (agent_id/team ciblés OU -1 = générique). SANS consommation."""
    rows = db.table("global_local_privilege").sql(
        "SELECT * FROM global_local_privilege WHERE kind = ? "
        "AND (agent_id = ? OR agent_id = -1) "
        "AND (team = ? OR team = -1) "
        "ORDER BY LENGTH(chemin_ref) DESC",
        (kind, int(agent_id), int(team)))
    return [r for r in rows if _priv_matches(r, chemin)]


def resolve_can_be_shared(db: Db, type_: str, ref: str = "",
                          data_id: Optional[int] = None,
                          tag_type: str = "", tag_value: str = "") -> str:
    """Cascade : (type,tag_type,tag_value) → (type,'*','*') → 'non'."""
    dtid = db.table("global_local_data_type").get({"code": type_})
    if not dtid:
        return "non"
    dt = dtid["data_type_id"]
    for tt, tv in ((tag_type, tag_value), ("*", "*")):
        r = db.table("global_local_shared_default").get(
            {"data_type_id": dt, "tag_type": tt or "*", "tag_value": tv or "*"})
        if r:
            return r["can_be_shared"]
    did = data_id if data_id is not None else data_id_of(ref)
    sh = db.table(f"{type_}_source_and_sharing").get({"data_id": did})
    return sh["can_be_shared"] if sh and sh["can_be_shared"] != "non" else "non"
