from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db, WriteDenied
from modules.sqlite.local import local as L  # helpers + infra logic


class DataTable:
    """Wrapper par TYPE de données du catalogue local.

    Un `DataTable` possède LES 4 tables d'un type et sait s'en servir :
    parsing/normalisation des valeurs (row() → colonnes data_value_*),
    tags, partage, list/search. Les tables sont créées dynamiquement par
    `create_new_data_type()`.

    data_id = hash stable(ref) ; (data_type_id, data_id) = clé primaire.
    """

    def __init__(self, db: Db, type_: str):
        self.db = db
        self.type = type_
        # tables (lazy via db.table)
        self.tbl = db.table(f"{type_}_data")
        self.tag_tbl = db.table(f"{type_}_tag")
        self.tag_type_tbl = db.table(f"{type_}_tag_type")
        self.sharing_tbl = db.table(f"{type_}_source_and_sharing")

    # ── DDL dynamique ─────────────────────────────────────────
    def add_row_column(self, header: str, value_type: str,
                       token: str = "") -> Dict[str, Any]:
        """add_colonne : étend la table row avec un nouveau header."""
        self._require_type()
        if not header or not header.replace("_", "").isalnum():
            raise ValueError(f"header invalide: {header!r}")
        cols = {c["name"] for c in self.db.columns(f"{self.type}_data")}
        col = f"data_value_{header}"
        if col in cols:
            return {"status": "exists", "column": col}
        self.db.sql(
            f"ALTER TABLE {self.type}_data ADD COLUMN {col} {L.col_type_for(value_type)}",
            token=token)
        return {"status": "ok", "column": col}

    # ── Data ──────────────────────────────────────────────────
    def upsert(self, payload: Dict[str, Any], token: str = "") -> Dict[str, Any]:
        """add/modify d'une data. row : valeurs des headers (row type)."""
        self._require_type()
        ref = payload.get("ref", "")
        if not ref:
            raise ValueError("ref requis")
        if self.db._mode == "ro":
            raise WriteDenied(f"{self.type}_data en lecture seule")
        dvt = str(payload.get("data_value_type", "json")).strip()
        if dvt not in L.SCALAR_VALUE_TYPES and dvt not in L.EXTERNAL_VALUE_TYPES \
                and not dvt.startswith("row(") and not dvt.startswith("text["):
            raise ValueError(f"data_value_type inconnu: {dvt!r}")
        if dvt == "file" and not payload.get("ref_file"):
            raise ValueError("data_value_type=file exige ref_file")

        did = L.data_id_of(ref)
        row = payload.get("row")
        parsed = L.parse_row_type(dvt)
        if parsed:
            value = json.dumps(row or {})
        else:
            v = payload.get("value", "{}")
            value = json.dumps(v) if not isinstance(v, str) else v

        cols = ["data_id", "ref", "name", "namespace", "version",
                "data_value_type", "value", "ref_file", "path", "description",
                "status", "last_modify"]
        params: List[Any] = [did, ref, payload.get("name") or ref,
                             payload.get("namespace", ""),
                             payload.get("version", "latest"), dvt, value,
                             payload.get("ref_file", ""), payload.get("path", ""),
                             payload.get("description", ""),
                             payload.get("status", "active"),
                             payload.get("last_modify") or L.now_iso()]
        with self.db.in_write() if token else self.db._lock:
            old = self.tbl.get({"data_id": did}, cols=["data_id"])
            if old:
                sets = ", ".join(f"{c}=?" for c in cols[2:]) + ", updated_at=?"
                self.db.sql(
                    f"UPDATE {self.type}_data SET {sets} WHERE data_id = ?",
                    params[2:] + [L.now_iso(), did], token=token)
                if parsed:
                    self._set_row_values(did, parsed, row, token)
            else:
                ph = ",".join("?" * len(params))
                self.db.sql(
                    f"INSERT INTO {self.type}_data ({','.join(cols)}) VALUES ({ph})",
                    params, token=token)
                if parsed:
                    self._set_row_values(did, parsed, row, token)
        return {"ok": True, "type": self.type, "data_id": did, "ref": ref}

    def _set_row_values(self, did: int, parsed: List[tuple],
                        row: Optional[Dict[str, Any]], token: str) -> None:
        for header, typ in parsed:
            col = f"data_value_{header}"
            # colonne peut exister (créée via add_row_column) ; sinon création auto
            cols = {c["name"] for c in self.db.columns(f"{self.type}_data")}
            if col not in cols:
                self.db.sql(
                    f"ALTER TABLE {self.type}_data ADD COLUMN {col} {L.col_type_for(typ)}",
                    token=token)
            if row and (header in row or col in row):
                v = row.get(header, row.get(col))
                if v is not None:
                    self.tbl.update({"data_id": did}, {col: L._coerce(typ, v)}, token=token)

    def get(self, ref: str = "", data_id: Optional[int] = None) -> Dict[str, Any]:
        """get par ref ou data_id. row → format plat {row: {header: value}}."""
        self._require_type()
        did = data_id if data_id is not None else L.data_id_of(ref)
        e = self.tbl.get({"data_id": did})
        if not e:
            raise KeyError(f"data introuvable: {self.type}/{ref or data_id}")
        dvt = e.get("data_value_type") or "json"
        parsed = L.parse_row_type(dvt)
        if parsed:
            row = {}
            for header, _typ in parsed:
                v = e.get(f"data_value_{header}")
                if v is not None:
                    row[header] = v
            e["row"] = row
        tags = self._tags(did)
        if tags:
            e["tags"] = tags
        sh = self.sharing_tbl.get({"data_id": did})
        if sh:
            e["sharing"] = sh
        return e

    def delete(self, ref: str = "", data_id: Optional[int] = None,
               token: str = "") -> Dict[str, Any]:
        self._require_type()
        did = data_id if data_id is not None else L.data_id_of(ref)
        n = self.tbl.remove({"data_id": did}, token=token)
        return {"ok": True, "deleted": n, "data_id": did}

    def list(self, namespace: str = "", page: int = 1, page_size: int = 50,
             sort: str = "name", order: str = "asc",
             status: str = "") -> Dict[str, Any]:
        w: Dict[str, Any] = {}
        if namespace:
            w["namespace"] = namespace
        if status:
            w["status"] = status
        sort = sort if sort in ("ref", "name", "namespace", "version",
                                "updated_at", "last_modify") else "name"
        order = "DESC" if order.lower() == "desc" else "ASC"
        total = self.tbl.count(w) if w else self.tbl.count()
        off = max(0, (page - 1) * page_size)
        cols = ["data_id", "ref", "name", "namespace", "version",
                "data_value_type", "status", "last_modify"]
        rows = self.tbl.select(where=w or None, cols=cols,
                               order_by=f"{sort} {order}",
                               limit=page_size, offset=off)
        return {"total": total, "page": page, "page_size": page_size,
                "items": rows}

    def list_recursive(self, namespace: str = "") -> List[Dict[str, Any]]:
        cols = ["data_id", "ref", "name", "namespace", "version", "status"]
        if not namespace:
            return self.tbl.select(cols=cols, order_by="namespace, name")
        return self.db.sql(
            f"SELECT {','.join(cols)} FROM {self.type}_data "
            "WHERE namespace = ? OR namespace LIKE ? ORDER BY namespace, name",
            (namespace, namespace + "/%"))

    def search(self, q: str = "", tag_type: str = "",
               tag_value: str = "") -> List[Dict[str, Any]]:
        cols = ["d.data_id", "d.ref", "d.name", "d.namespace",
                "d.version", "d.status"]
        if tag_type:
            w = ("JOIN {t}_tag tg ON tg.data_id = d.data_id "
                 "WHERE tg.tag_type = ?".format(t=self.type))
            args: List[Any] = [tag_type]
            if tag_value is not None:
                w += " AND tg.tag_value = ?"
                args.append(str(tag_value))
            rows = self.db.sql(
                f"SELECT DISTINCT {','.join(cols)} FROM {self.type}_data d "
                + w + (" ORDER BY d.name" if q else ""), args)
            return rows
        args2: List[Any] = []
        w2 = ""
        if q:
            w2 = "WHERE d.name LIKE ? OR d.ref LIKE ? OR d.description LIKE ?"
            args2 = [f"%{q}%"] * 3
        return self.db.sql(
            f"SELECT {','.join(cols)} FROM {self.type}_data d " + w2
            + (" ORDER BY d.name" if q else ""), args2)

    def refresh(self, data_id: int, last_access: str = "") -> Dict[str, Any]:
        """Refresh conditionnel : data si last_modify > last_access sinon
        {"modified": False} (sémantique 304). Aucune écriture."""
        self._require_type()
        e = self.tbl.get({"data_id": data_id}, cols=["data_id", "ref", "last_modify"])
        if not e:
            raise KeyError(f"data introuvable: {self.type}/{data_id}")
        if last_access and e["last_modify"] and str(e["last_modify"]) <= str(last_access):
            return {"modified": False, "data_id": data_id, "ref": e["ref"]}
        return {"modified": True, "data_id": data_id, "ref": e["ref"],
                "last_modify": e["last_modify"]}

    # ── Tags ──────────────────────────────────────────────────
    def tag_type_add(self, tag_type: str, tag_value_type: str = "text",
                     description: str = "", token: str = "") -> Dict[str, Any]:
        self._require_type()
        if tag_value_type not in L.TAG_VALUE_TYPES:
            raise ValueError(f"tag_value_type inconnu: {tag_value_type!r}")
        self.tag_type_tbl.upsert(
            {"tag_type": tag_type, "tag_value_type": tag_value_type,
             "description": description}, ["tag_type"], token=token)
        return {"ok": True, "type": self.type, "tag_type": tag_type,
                "tag_value_type": tag_value_type}

    def list_tag_types(self) -> List[Dict[str, Any]]:
        return self.tag_type_tbl.select(order_by="tag_type")

    def tag_attach(self, ref: str = "", data_id: Optional[int] = None,
                   tag_type: str = "", tag_value: Any = None,
                   token: str = "") -> Dict[str, Any]:
        self._require_type()
        if not tag_type or tag_value is None:
            raise ValueError("tag_type + tag_value requis")
        tt = self.tag_type_tbl.get({"tag_type": tag_type})
        if not tt:
            raise ValueError(f"tag_type non déclaré: {tag_type!r}")
        tv = str(tag_value)
        if isinstance(tag_value, (list, dict)):
            tv = json.dumps(tag_value)
        did = data_id if data_id is not None else L.data_id_of(ref)
        self.tag_tbl.upsert({"data_id": did, "tag_type": tag_type,
                             "tag_value": tv}, ["data_id", "tag_type", "tag_value"],
                            token=token)
        return {"ok": True, "type": self.type, "data_id": did,
                "tag_type": tag_type, "tag_value": tv}

    def tag_detach(self, ref: str = "", data_id: Optional[int] = None,
                   tag_type: str = "", tag_value: Any = None,
                   token: str = "") -> Dict[str, Any]:
        self._require_type()
        did = data_id if data_id is not None else L.data_id_of(ref)
        where: Dict[str, Any] = {"data_id": did, "tag_type": tag_type}
        if tag_value is not None:
            where["tag_value"] = str(tag_value)
        self.tag_tbl.remove(where, token=token)
        return {"ok": True, "data_id": did}

    def tags_by_value(self, tag_type: str,
                      tag_value: Any = None) -> List[Dict[str, Any]]:
        self._require_type()
        if tag_value is None:
            return self.db.sql(
                f"SELECT tg.data_id, d.ref, d.name, tg.tag_type, tg.tag_value "
                f"FROM {self.type}_tag tg JOIN {self.type}_data d "
                f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? "
                f"ORDER BY tg.tag_value", (tag_type,))
        return self.db.sql(
            f"SELECT tg.data_id, d.ref, d.name, tg.tag_type, tg.tag_value "
            f"FROM {self.type}_tag tg JOIN {self.type}_data d "
            f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? AND tg.tag_value = ? "
            f"ORDER BY d.name", (tag_type, str(tag_value)))

    def _tags(self, data_id: int) -> List[Dict[str, Any]]:
        return self.tag_tbl.select(where={"data_id": data_id},
                                   cols=["tag_type", "tag_value"])

    # ── Sharing ───────────────────────────────────────────────
    def set_sharing(self, ref: str = "", data_id: Optional[int] = None,
                    source: str = "perso", source_url: str = "",
                    is_from_share: bool = False, is_it_shared: bool = False,
                    can_be_shared: str = "non", token: str = "") -> Dict[str, Any]:
        self._require_type()
        if can_be_shared not in L.SHARING_LEVELS:
            raise ValueError(f"can_be_shared invalide: {can_be_shared!r}")
        did = data_id if data_id is not None else L.data_id_of(ref)
        self.sharing_tbl.upsert(
            {"data_id": did, "source": source, "source_url": source_url,
             "is_from_share": 1 if is_from_share else 0,
             "is_it_shared": 1 if is_it_shared else 0,
             "can_be_shared": can_be_shared}, ["data_id"], token=token)
        return {"ok": True, "data_id": did}

    def get_sharing(self, ref: str = "", data_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        self._require_type()
        did = data_id if data_id is not None else L.data_id_of(ref)
        return self.sharing_tbl.get({"data_id": did})

    # ── Interne ────────────────────────────────────────────────
    def _require_type(self) -> None:
        if not self.db._exists(f"{self.type}_data"):
            raise KeyError(f"type inconnu: {self.type}")


def create_new_data_type(db: Db, type_: str, description: str = "",
                         token: str = "") -> Dict[str, Any]:
    """Crée un type à chaud : 4 tables + index + registre data_type. Retourne
    le wrapper DataTable."""
    db.check_write(token)
    if L.is_reserved(type_):
        raise ValueError(f"type réservé: {type_!r}")
    if not type_ or not type_.replace("_", "").isalnum():
        raise ValueError(f"type de données invalide: {type_!r}")
    data, tag, tag_type, sharing = (f"{type_}_data", f"{type_}_tag",
                                    f"{type_}_tag_type", f"{type_}_source_and_sharing")
    base = ",\n    ".join(f"{c} {d}" for c, d in L.BASE_COLUMNS.items())
    db.sql(f"CREATE TABLE IF NOT EXISTS {data} (\n    {base}\n);", token=token)
    db.sql(f"CREATE INDEX IF NOT EXISTS idx_{data}_ns ON {data}(namespace);", token=token)
    db.sql(f"CREATE INDEX IF NOT EXISTS idx_{data}_name ON {data}(name);", token=token)
    db.sql(f"""CREATE TABLE IF NOT EXISTS {tag} (
        tag_id     INTEGER PRIMARY KEY AUTOINCREMENT,
        data_id    INTEGER NOT NULL REFERENCES {data}(data_id) ON DELETE CASCADE,
        tag_type   TEXT NOT NULL,
        tag_value  TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(data_id, tag_type, tag_value)
    );""", token=token)
    db.sql(f"CREATE INDEX IF NOT EXISTS idx_{tag}_type ON {tag}(tag_type, tag_value);", token=token)
    db.sql(f"""CREATE TABLE IF NOT EXISTS {tag_type} (
        tag_type       TEXT PRIMARY KEY,
        tag_value_type TEXT NOT NULL DEFAULT 'text'
                       CHECK(tag_value_type IN ('bool','text','number','date','list','range')),
        description    TEXT DEFAULT '',
        created_at     TEXT DEFAULT (datetime('now'))
    );""", token=token)
    db.sql(f"""CREATE TABLE IF NOT EXISTS {sharing} (
        data_id       INTEGER PRIMARY KEY REFERENCES {data}(data_id) ON DELETE CASCADE,
        source        TEXT NOT NULL DEFAULT 'perso',
        source_url    TEXT DEFAULT '',
        is_from_share INTEGER NOT NULL DEFAULT 0,
        is_it_shared  INTEGER NOT NULL DEFAULT 0,
        can_be_shared TEXT NOT NULL DEFAULT 'non',
        shared_at     TEXT DEFAULT NULL,
        created_at    TEXT DEFAULT (datetime('now'))
    );""", token=token)
    db.table("global_local_data_type").upsert(
        {"code": type_, "description": description or type_}, ["code"], token=token)
    return {"status": "ok", "type": type_}


def get_table(db: Db, type_: str) -> DataTable:
    """Retourne le wrapper d'un type existant (ou lève KeyError)."""
    t = DataTable(db, type_)
    t._require_type()
    return t


def list_data_types(db: Db) -> List[Dict[str, Any]]:
    return db.table("global_local_data_type").select(
        cols=["data_type_id", "code", "description", "active", "created_at"],
        order_by="code")


def delete_type(db: Db, type_: str, token: str = "") -> Dict[str, Any]:
    db.check_write(token)
    if L.is_reserved(type_):
        raise ValueError(f"type réservé: {type_!r}")
    for t in (f"{type_}_data", f"{type_}_tag", f"{type_}_tag_type",
              f"{type_}_source_and_sharing"):
        if db._exists(t):
            db.sql(f"DROP TABLE IF EXISTS {t}", token=token)
    db.table("global_local_data_type").remove({"code": type_}, token=token)
    return {"status": "ok", "type": type_}


def activate_type(db: Db, type_: str, active: bool, token: str = "") -> Dict[str, Any]:
    db.check_write(token)
    db.table("global_local_data_type").upsert(
        {"code": type_, "active": 1 if active else 0}, ["code"], token=token)
    return {"status": "ok", "type": type_, "active": active}
