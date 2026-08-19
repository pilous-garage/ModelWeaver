from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db, WriteDenied
from modules.sqlite.local import local as L


class DataTable:
    """Wrapper par TYPE de données du catalogue local.

    Un `DataTable` possède LES 4 tables d'un type et sait s'en servir :
    parsing/normalisation des valeurs (row() → colonnes data_value_*),
    tags, partage, list/search. Les tables sont créées dynamiquement par
    `create_new_data_type()`.

    V4 : une entrée = un quadruplé (namespace, name, source_id, version)
    UNIQUE, chaque entrée a son propre data_id = hash stable du quadruplé.
    Accès par sélection : tag (prioritaire) → source (préférence) → version
    (newest/oldest/littérale) — voir local.parse_accessor / select_row.
    """

    def __init__(self, db: Db, type_: str):
        self.db = db
        self.type = type_
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
        self.db._cols_cache.pop(f"{self.type}_data", None)
        return {"status": "ok", "column": col}

    # ── Data ──────────────────────────────────────────────────
    def upsert(self, payload: Dict[str, Any], source: Any = "user",
               token: str = "") -> Dict[str, Any]:
        """add d'une data : ligne du quadruplé (namespace, name, source,
        version). Si la ligne de SA source + version existe déjà → mise à
        jour en place (last_modify) ; sinon INSERT d'une NOUVELLE entrée.
        Le writer ne touche JAMAIS une ligne d'une autre source (unicité par
        source : chaque source a sa propre ligne de version)."""
        self._require_type()
        if self.db._mode == "ro":
            raise WriteDenied(f"{self.type}_data en lecture seule")
        sid = L.source_id_for(self.db, source)
        ns = payload.get("namespace", "")
        name = payload.get("name", "")
        if not name:
            raise ValueError("name requis")
        ver = payload.get("version", "latest")
        dvt = str(payload.get("data_value_type", "json")).strip()
        if dvt not in L.SCALAR_VALUE_TYPES and dvt not in L.EXTERNAL_VALUE_TYPES \
                and not dvt.startswith("row(") and not dvt.startswith("text["):
            raise ValueError(f"data_value_type inconnu: {dvt!r}")
        if dvt == "file" and not payload.get("ref_file"):
            raise ValueError("data_value_type=file exige ref_file")

        did = L.data_id_of(ns, name, sid, ver)
        parsed = L.parse_row_type(dvt)
        if parsed:
            value = json.dumps(payload.get("row") or {})
        else:
            v = payload.get("value", "{}")
            value = json.dumps(v) if not isinstance(v, str) else v

        cols = ["data_id", "name", "namespace", "source_id", "version",
                "data_value_type", "value", "ref_file", "path", "description",
                "status", "last_modify"]
        params: List[Any] = [did, name, ns, sid, ver, dvt, value,
                             payload.get("ref_file", ""), payload.get("path", ""),
                             payload.get("description", ""),
                             payload.get("status", "active"),
                             payload.get("last_modify") or L.now_iso()]
        for ec in L.EXTRA_COLS_ALLOWED:
            if ec in payload:
                cols.append(ec)
                params.append(payload[ec])
        with self.db.in_write() if token else self.db._lock:
            old = self.tbl.get({"data_id": did})
            if old:
                sets = ", ".join(f"{c}=?" for c in cols[2:]) + ", updated_at=?"
                self.db.sql(
                    f"UPDATE {self.type}_data SET {sets} WHERE data_id = ?",
                    params[2:] + [L.now_iso(), did], token=token)
                if parsed:
                    self._set_row_values(did, parsed, payload.get("row"), token)
            else:
                ph = ",".join("?" * len(params))
                self.db.sql(
                    f"INSERT INTO {self.type}_data ({','.join(cols)}) VALUES ({ph})",
                    params, token=token)
                if parsed:
                    self._set_row_values(did, parsed, payload.get("row"), token)
        return {"ok": True, "type": self.type, "data_id": did,
                "namespace": ns, "name": name, "source": source, "version": ver}

    def modify(self, accessor: str, payload: Dict[str, Any],
               source: Any = "user", token: str = "") -> Dict[str, Any]:
        """modify d'une data.

        - La ligne sélectionnée est de SA source → mise à jour EN PLACE
          (conservation de l'identité (ns, name, source, version)).
        - La ligne sélectionnée est d'une AUTRE source (ou absente) → on ne
          l'écrase JAMAIS : création d'une NOUVELLE entrée de source donnée
          (version = payload["version"], sinon version suivante de sa
          source)."""
        sel = L.parse_accessor(self.type, accessor)
        row = L.select_row(self.db, self.type, sel["namespace"], sel["name"], sel)
        sid = L.source_id_for(self.db, source)
        if row and row["source_id"] == sid:
            payload = {**payload, "namespace": row["namespace"],
                       "name": row["name"], "version": row["version"]}
        else:
            ver = payload.get("version", "")
            if not ver:
                ver = L.bump_version(row["version"]) if row else "latest"
            payload = {**payload, "namespace": sel["namespace"],
                       "name": sel["name"], "version": ver}
        return self.upsert(payload, source=source, token=token)

    def _set_row_values(self, did: int, parsed: List[tuple],
                        row: Optional[Dict[str, Any]], token: str) -> None:
        for header, typ in parsed:
            col = f"data_value_{header}"
            cols = {c["name"] for c in self.db.columns(f"{self.type}_data")}
            if col not in cols:
                self.db.sql(
                    f"ALTER TABLE {self.type}_data ADD COLUMN {col} {L.col_type_for(typ)}",
                    token=token)
                self.db._cols_cache.pop(f"{self.type}_data", None)
            if row and (header in row or col in row):
                v = row.get(header, row.get(col))
                if v is not None:
                    self.tbl.update({"data_id": did}, {col: L.coerce(typ, v)},
                                    token=token)

    def get(self, accessor: str) -> Dict[str, Any]:
        """get par adresse + sélecteurs (tag → source → version).
        row → format plat {row: {header: value}}."""
        self._require_type()
        sel = L.parse_accessor(self.type, accessor)
        e = L.select_row(self.db, self.type, sel["namespace"], sel["name"], sel)
        if not e:
            raise KeyError(f"data introuvable: {self.type}/{accessor}")
        did = e["data_id"]
        e["source_type"] = L.source_type_of(self.db, e["source_id"])
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

    def delete(self, accessor: str, source: Any = "user",
               token: str = "") -> Dict[str, Any]:
        """delete de la ligne sélectionnée. Garde : un writer ne supprime
        que les lignes de SA source (source étrangère → WriteDenied)."""
        self._require_type()
        sel = L.parse_accessor(self.type, accessor)
        row = L.select_row(self.db, self.type, sel["namespace"], sel["name"], sel)
        if not row:
            return {"ok": True, "deleted": 0, "type": self.type}
        sid = L.source_id_for(self.db, source)
        if row["source_id"] != sid:
            raise WriteDenied(
                f"suppression refusée : {self.type}/{accessor} est de source "
                f"{L.source_type_of(self.db, row['source_id'])} (writer {source})")
        n = self.tbl.remove({"data_id": row["data_id"]}, token=token)
        return {"ok": True, "deleted": n, "data_id": row["data_id"]}

    def list_versions(self, namespace: str = "", name: str = "",
                      source: Any = None) -> List[Dict[str, Any]]:
        """Toutes les versions/sources d'une famille (tri version DESC)."""
        w: Dict[str, Any] = {"name": name} if name else {}
        if namespace:
            w["namespace"] = namespace
        if source is not None:
            w["source_id"] = L.source_id_for(self.db, source)
        rows = self.tbl.select(where=w or None,
                               order_by="version DESC")
        for r in rows:
            r["source_type"] = L.source_type_of(self.db, r["source_id"])
        return rows

    def list(self, namespace: str = "", page: int = 1, page_size: int = 50,
             sort: str = "name", order: str = "asc",
             status: str = "") -> Dict[str, Any]:
        w: Dict[str, Any] = {}
        if namespace:
            w["d.namespace"] = namespace
        if status:
            w["d.status"] = status
        sort = sort if sort in ("name", "namespace", "version",
                                "updated_at", "last_modify") else "name"
        order = "DESC" if order.lower() == "desc" else "ASC"
        cond = (" WHERE " + " AND ".join(f"{k}=?" for k in w)) if w else ""
        args: List[Any] = list(w.values())
        total = self.tbl.count(
            {k[2:]: v for k, v in w.items()}) if w else self.tbl.count()
        rows = self.db.sql(
            f"SELECT d.data_id, d.name, d.namespace, d.source_id, d.version, "
            f"d.data_value_type, d.status, d.last_modify, "
            f"s.type_source AS source_type "
            f"FROM {self.type}_data d JOIN global_local_source s "
            f"ON s.sources_id = d.source_id{cond} "
            f"ORDER BY d.{sort} {order}, d.version DESC "
            f"LIMIT ? OFFSET ?", args + [page_size, max(0, (page - 1) * page_size)])
        return {"total": total, "page": page, "page_size": page_size,
                "items": rows}

    def list_recursive(self, namespace: str = "") -> List[Dict[str, Any]]:
        cols = "d.data_id, d.name, d.namespace, d.source_id, d.version, d.status, s.type_source AS source_type"
        if not namespace:
            return self.db.sql(
                f"SELECT {cols} FROM {self.type}_data d "
                "JOIN global_local_source s ON s.sources_id = d.source_id "
                "ORDER BY d.namespace, d.name, d.version")
        return self.db.sql(
            f"SELECT {cols} FROM {self.type}_data d "
            "JOIN global_local_source s ON s.sources_id = d.source_id "
            "WHERE d.namespace = ? OR d.namespace LIKE ? "
            "ORDER BY d.namespace, d.name, d.version",
            (namespace, namespace + "/%"))

    def search(self, q: str = "", tag_type: str = "",
               tag_value: str = "") -> List[Dict[str, Any]]:
        cols = ("d.data_id, d.name, d.namespace, d.source_id, d.version, "
                "d.status, s.type_source AS source_type")
        if tag_type:
            w = (f"JOIN {self.type}_tag tg ON tg.data_id = d.data_id "
                 "JOIN global_local_source s ON s.sources_id = d.source_id "
                 "WHERE tg.tag_type = ?")
            args: List[Any] = [tag_type]
            if tag_value is not None:
                w += " AND tg.tag_value = ?"
                args.append(str(tag_value))
            rows = self.db.sql(
                f"SELECT DISTINCT {cols} FROM {self.type}_data d "
                + w + (" ORDER BY d.name" if q else ""), args)
            return rows
        args2: List[Any] = []
        w2 = ("JOIN global_local_source s ON s.sources_id = d.source_id")
        if q:
            w2 += (" WHERE d.name LIKE ? OR d.namespace LIKE ? "
                   "OR d.description LIKE ?")
            args2 = [f"%{q}%"] * 3
        return self.db.sql(
            f"SELECT {cols} FROM {self.type}_data d " + w2
            + (" ORDER BY d.name" if q else ""), args2)

    def refresh(self, accessor: str, last_access: str = "") -> Dict[str, Any]:
        """Refresh conditionnel de la ligne sélectionnée : data si
        last_modify > last_access sinon {"modified": False} (304).
        Aucune écriture."""
        self._require_type()
        sel = L.parse_accessor(self.type, accessor)
        e = L.select_row(self.db, self.type, sel["namespace"], sel["name"], sel,
                         cols=["data_id", "name", "last_modify"])
        if not e:
            raise KeyError(f"data introuvable: {self.type}/{accessor}")
        if last_access and e["last_modify"] and str(e["last_modify"]) <= str(last_access):
            return {"modified": False, "data_id": e["data_id"],
                    "name": e["name"]}
        return {"modified": True, "data_id": e["data_id"], "name": e["name"],
                "last_modify": e["last_modify"]}

    # ── Tags (par entrée = par version/source) ─────────────────
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

    def tag_attach(self, accessor: str, tag_type: str = "", tag_value: Any = None,
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
        did = self._resolve_data_id(accessor)
        self.tag_tbl.upsert({"data_id": did, "tag_type": tag_type,
                             "tag_value": tv}, ["data_id", "tag_type", "tag_value"],
                            token=token)
        return {"ok": True, "type": self.type, "data_id": did,
                "tag_type": tag_type, "tag_value": tv}

    def tag_detach(self, accessor: str, tag_type: str = "",
                   tag_value: Any = None, token: str = "") -> Dict[str, Any]:
        self._require_type()
        did = self._resolve_data_id(accessor)
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
                f"SELECT tg.data_id, d.name, d.namespace, d.version, "
                f"tg.tag_type, tg.tag_value "
                f"FROM {self.type}_tag tg JOIN {self.type}_data d "
                f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? "
                f"ORDER BY tg.tag_value", (tag_type,))
        return self.db.sql(
            f"SELECT tg.data_id, d.name, d.namespace, d.version, "
            f"tg.tag_type, tg.tag_value "
            f"FROM {self.type}_tag tg JOIN {self.type}_data d "
            f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? AND tg.tag_value = ? "
            f"ORDER BY d.name", (tag_type, str(tag_value)))

    def _tags(self, data_id: int) -> List[Dict[str, Any]]:
        return self.tag_tbl.select(where={"data_id": data_id},
                                   cols=["tag_type", "tag_value"])

    # ── Sharing (par entrée) ───────────────────────────────────
    def set_sharing(self, accessor: str, source: str = "perso",
                    source_url: str = "", is_from_share: bool = False,
                    is_it_shared: bool = False, can_be_shared: str = "non",
                    token: str = "") -> Dict[str, Any]:
        self._require_type()
        if can_be_shared not in L.SHARING_LEVELS:
            raise ValueError(f"can_be_shared invalide: {can_be_shared!r}")
        did = self._resolve_data_id(accessor)
        self.sharing_tbl.upsert(
            {"data_id": did, "source": source, "source_url": source_url,
             "is_from_share": 1 if is_from_share else 0,
             "is_it_shared": 1 if is_it_shared else 0,
             "can_be_shared": can_be_shared}, ["data_id"], token=token)
        return {"ok": True, "data_id": did}

    def get_sharing(self, accessor: str) -> Optional[Dict[str, Any]]:
        self._require_type()
        did = self._resolve_data_id(accessor)
        return self.sharing_tbl.get({"data_id": did})

    # ── Interne ────────────────────────────────────────────────
    def _resolve_data_id(self, accessor: str) -> int:
        sel = L.parse_accessor(self.type, accessor)
        row = L.select_row(self.db, self.type, sel["namespace"], sel["name"], sel)
        if not row:
            raise KeyError(f"data introuvable: {self.type}/{accessor}")
        return row["data_id"]

    def _require_type(self) -> None:
        if not self.db._exists(f"{self.type}_data"):
            raise KeyError(f"type inconnu: {self.type}")


def create_new_data_type(db: Db, type_: str, description: str = "",
                         extra_cols: Optional[Dict[str, str]] = None,
                         token: str = "") -> Dict[str, Any]:
    """Crée un type à chaud : 4 tables + index + registre data_type.

    `extra_cols` : colonnes spécifiques au type (ex. model_official_id sur
    model_provider_endpoint) — ajoutées à BASE_COLUMNS au CREATE, et
    ALTER TABLE ADD COLUMN si la table existait déjà SANS elles."""
    db.check_write(token)
    if L.is_reserved(type_):
        raise ValueError(f"type réservé: {type_!r}")
    if not type_ or not type_.replace("_", "").isalnum():
        raise ValueError(f"type de données invalide: {type_!r}")
    data, tag, tag_type, sharing = (f"{type_}_data", f"{type_}_tag",
                                    f"{type_}_tag_type", f"{type_}_source_and_sharing")
    existed = db._exists(data)
    cols = dict(L.BASE_COLUMNS)
    if extra_cols:
        cols.update(extra_cols)
    base = ",\n    ".join(f"{c} {d}" for c, d in cols.items())
    db.sql(f"CREATE TABLE IF NOT EXISTS {data} (\n    {base},\n    {L.BASE_UNIQUE}\n);",
           token=token)
    for c, d in (extra_cols or {}).items():
        known = [r["name"] for r in db.sql(f"PRAGMA table_info({data})")]
        if c not in known:
            db.sql(f"ALTER TABLE {data} ADD COLUMN {c} {d}", token=token)
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
    return {"status": "created" if not existed else "exists", "type": type_}


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