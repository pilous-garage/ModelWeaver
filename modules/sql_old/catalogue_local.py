"""LocalCatalogue — Référentiel méta des données (V2, spec local_catalogue_spec.md).

Référentiel des données LÉGÈRES et typées :
  - data_value_type : text[n]ch, string, int, uint, float, bool, date,
    timestamp, json, file, row(header=type, …).
  - data_id = hash stable(ref) par type (int64) — identique entre
    load/reload ; PAS d'AUTOINCREMENT. Couple (data_type_id, data_id).
  - Tables PAR TYPE créées à chaud (create_type) : {type}_data/_tag/
    _tag_type/_source_and_sharing ; tables fixes global_local_*.
  - Non-bloquant : lectures mode=ro directes ; UN SEUL writer (token
    write_catalogue) en mini-batchs ; dernière trace = last_modify.
  - Écritures externes via global_local_buffer_op : un importeur dépose
    ses ops 'pending' en une écriture ; process_buffer applique en
    mini-batchs (lecteur jamais bloqué).
  - Famille SECURITY : global_local_privilege(+conditions) +
    global_local_security_supervisor, writer distinct (token privé).
"""

from __future__ import annotations

import json
import re as _re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import xxhash

from modules.sql.schema import (
    _default_local_catalogue_db, _row_to_dict, _rows_to_list,
    _add_column_if_missing,
)

SCHEMA = Path(__file__).resolve().parent / "local_catalogue_schema.sql"

_TEXT_N = _re.compile(r"^text\[\d+ch\]$")

# Niveau d'importance maximal (priorité absolue = "toujours").
MAX_PRIV_LEVEL = (1 << 32) - 1   # MAX_UINT32 = 4294967295

# Niveaux de DEMANDE (ask) — du moins au plus restrictif.
ASK_LEVELS = ("none", "security_supervisor", "human", "human_root")

# Valeurs de partage autorisées (source_and_sharing.can_be_shared).
SHARING_LEVELS = ("non", "everyone", "enterprise", "friends", "official")

# Sources possibles d'une donnée.
SOURCE_VALUES = ("perso", "distant", "enterprise", "github/depot", "official")

# Types de valeur d'un TAG (registre {type}_tag_type).
TAG_VALUE_TYPES = ("bool", "text", "number", "date", "list", "range")

# Types de VALEUR d'une DATA (data_value_type) — scalaires + file + row.
SCALAR_VALUE_TYPES = ("string", "int", "uint", "float", "bool", "date",
                      "timestamp", "json")
EXTERNAL_VALUE_TYPES = ("file",)

# Statuts d'une data.
DATA_STATUSES = ("active", "missing", "archived")

# Espace de nommage RÉSERVÉ aux tests de check officiels (inchangé V1).
RESERVED_TEST_PREFIX = "auto-test-check-official"
RESERVED_SYSTEM_PREFIXES = ("auto-", "system/", "catalogue/")

# Colonnes socle d'une table {type}_data (V2). data_id = PK (hash stable),
# PAS d'AUTOINCREMENT. Pas de colonne data_type (implicite par table).
BASE_COLUMNS = {
    "data_id": "INTEGER PRIMARY KEY",          # hash stable(ref), int64 positif
    "ref": "TEXT UNIQUE NOT NULL",
    "name": "TEXT DEFAULT ''",
    "namespace": "TEXT DEFAULT ''",
    "version": "TEXT DEFAULT 'latest'",
    "data_value_type": "TEXT NOT NULL",        # text[200ch]|string|…|file|row(…)
    "value": "TEXT DEFAULT '{}'",              # scalaire/json (row = plat) ; jamais lourd
    "ref_file": "TEXT DEFAULT ''",             # adresse absolue du fichier (file)
    "path": "TEXT DEFAULT ''",                 # chemin symbolique (global_local_path)
    "description": "TEXT DEFAULT ''",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "last_modify": "TEXT DEFAULT NULL",        # seule trace, posée par le writer
    "created_at": "TEXT DEFAULT (datetime('now'))",
    "updated_at": "TEXT DEFAULT (datetime('now'))",
}

# Types SQLite cibles par data_value_type scalaire (colonnes data_value_*).
_TYPE_TO_SQL = {
    "text": "TEXT", "string": "TEXT", "date": "TEXT", "json": "TEXT",
    "file": "TEXT", "row": "TEXT",
    "int": "INTEGER", "uint": "INTEGER", "bool": "INTEGER", "timestamp": "INTEGER",
    "float": "REAL",
}

# Types SQLite par tag_value_type.
_TAG_TYPE_TO_SQL = {
    "bool": "INTEGER", "text": "TEXT", "number": "REAL",
    "date": "TEXT", "list": "TEXT", "range": "TEXT",
}

_SEED = 0x5EEDC0DE


def data_id_of(ref: str) -> int:
    """Hash stable (int64 positif) de la ref — identique entre load/reload
    et entre RAM/HDD. Pas de collision prévue (xxhash64)."""
    return xxhash.xxh64(str(ref), seed=_SEED).intdigest() & 0x7FFFFFFFFFFFFFFF


def _parse_row_type(dvt: str) -> Optional[List[Tuple[str, str]]]:
    """Parse 'row(header=type, h2=type2, …)' → [(header, type), …].
    Récursif : un type peut être 'row(...)' (imbrication)."""
    s = dvt.strip()
    if not (s.startswith("row(") and s.endswith(")")):
        return None
    inner = s[4:-1].strip()
    if not inner:
        return None
    out: List[Tuple[str, str]] = []
    for part in inner.split(","):
        part = part.strip()
        if "=" not in part:
            raise ValueError(f"row invalide: {part!r} dans {dvt!r}")
        header, typ = part.split("=", 1)
        header = header.strip()
        typ = typ.strip()
        if not header or not typ:
            raise ValueError(f"row invalide: {dvt!r}")
        out.append((header, typ))
    return out


def _col_type_for(dvt_part: str) -> str:
    """Type SQLite de la colonne data_value_* pour un type de valeur."""
    p = dvt_part.strip()
    if p == "row":
        return "TEXT"
    if p in _TYPE_TO_SQL:
        return _TYPE_TO_SQL[p]
    m = _TEXT_N.match(p)
    if m:
        return "TEXT"
    raise ValueError(f"type de valeur inconnu: {dvt_part!r}")


class CatalogueTypeNotFound(KeyError):
    pass


class WriteDenied(PermissionError):
    pass


class LocalCatalogue:
    """Connexion + repo du domaine local_catalogue (V2).

    Deux modes :
      - read_only (défaut) : connexion `mode=ro` — toute écriture lève une
        erreur SQLite (garanti par le moteur).
      - writer : connexion `rwc` — utilisée par write_catalogue (le seul
        writer autorisé). Toute opération d'écriture exige le token.
    """

    def __init__(self, db_path: Optional[Path] = None,
                 mode: str = "ro", write_token: str = ""):
        self.db_path = Path(db_path) if db_path else _default_local_catalogue_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._write_token = write_token
        self._lock = threading.RLock()
        uri = f"file:{self.db_path}?mode={'ro' if mode == 'ro' else 'rwc'}"
        self.conn = sqlite3.connect(uri, uri=True, timeout=30,
                                    check_same_thread=(mode == "rwc"))
        self.conn.row_factory = sqlite3.Row
        if mode != "ro":
            self.conn.execute("PRAGMA journal_mode=WAL")
            self.conn.execute("PRAGMA foreign_keys=ON")
        self._ensure_schema()

    # ── Schéma ──────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        """Writer : reset une seule fois (schema_version < 2) puis vérifs
        idempotentes. Lecteur ro : aucune écriture, simple contrôle."""
        if self._mode == "ro":
            t = self.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='global_local_meta'").fetchone()
            if not t:
                raise RuntimeError(
                    "catalogue local non initialisé (faire tourner le writer "
                    "une fois d'abord)")
            return
        ver = 0
        try:
            row = self.conn.execute(
                "SELECT value FROM global_local_meta "
                "WHERE key='schema_version'").fetchone()
            ver = int(row["value"]) if row else 0
        except Exception:
            ver = 0
        if ver < 2:
            # MIGRATION : reset complet volontaire (spec §10) — drop+create+seed.
            self.conn.executescript(SCHEMA.read_text())
        # Vérifs idempotentes (colonnes manquantes sur BDD déjà V2).
        _add_column_if_missing(self.conn, "global_local_data_type", "active",
                               "INTEGER NOT NULL DEFAULT 1")
        self.conn.commit()

    def close(self) -> None:
        try:
            self.conn.close()
        except Exception:
            pass

    # ── Autorité writer ─────────────────────────────────────

    def _check_write(self, token: str) -> None:
        if self._mode == "ro":
            raise WriteDenied("catalogue_local en lecture seule (mode=ro)")
        if not self._write_token or token != self._write_token:
            raise WriteDenied("token writer invalide")

    @staticmethod
    def _is_reserved(ref: str) -> bool:
        return (ref.startswith(RESERVED_TEST_PREFIX)
                or any(ref.startswith(p) for p in RESERVED_SYSTEM_PREFIXES)
                or ref in ("",))

    def _check_reserved(self, ref: str, allow_tests: bool = True) -> None:
        if self._is_reserved(ref):
            if allow_tests and ref.startswith(RESERVED_TEST_PREFIX):
                return
            raise ValueError(f"ref réservée: {ref!r}")

    def _tables_for(self, type_: str) -> Tuple[str, str, str, str]:
        return (f"{type_}_data", f"{type_}_tag", f"{type_}_tag_type",
                f"{type_}_source_and_sharing")

    def _exists_table(self, table: str) -> bool:
        r = self.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        return r is not None

    def _check_type(self, type_: str) -> None:
        if not self._exists_table(f"{type_}_data"):
            raise CatalogueTypeNotFound(f"type inconnu: {type_}")

    def _touch(self, table: str, data_id: int) -> None:
        self.conn.execute(
            f"UPDATE {table} SET updated_at = datetime('now'), "
            f"last_modify = datetime('now') WHERE data_id = ?", (data_id,))

    # ── Registre des types ──────────────────────────────────

    def create_type(self, type_: str, description: str = "",
                    token: str = "") -> Dict[str, Any]:
        """Crée un type à chaud : 4 tables {type}_* + registre data_type."""
        self._check_write(token)
        self._check_reserved(type_)
        if not type_ or not type_.replace("_", "").isalnum():
            raise ValueError(f"type de données invalide: {type_!r}")
        data, tag, tag_type, sharing = self._tables_for(type_)
        base = ",\n    ".join(f"{c} {d}" for c, d in BASE_COLUMNS.items())
        with self._lock:
            self.conn.executescript(f"""
CREATE TABLE IF NOT EXISTS {data} (
    {base}
);
CREATE INDEX IF NOT EXISTS idx_{data}_ns ON {data}(namespace);
CREATE INDEX IF NOT EXISTS idx_{data}_name ON {data}(name);

CREATE TABLE IF NOT EXISTS {tag} (
    tag_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    data_id     INTEGER NOT NULL REFERENCES {data}(data_id) ON DELETE CASCADE,
    tag_type    TEXT NOT NULL,
    tag_value   TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(data_id, tag_type, tag_value)
);
CREATE INDEX IF NOT EXISTS idx_{tag}_type ON {tag}(tag_type, tag_value);

CREATE TABLE IF NOT EXISTS {tag_type} (
    tag_type       TEXT PRIMARY KEY,
    tag_value_type TEXT NOT NULL DEFAULT 'text'
                   CHECK(tag_value_type IN ('bool','text','number','date','list','range')),
    description    TEXT DEFAULT '',
    created_at     TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS {sharing} (
    data_id       INTEGER PRIMARY KEY REFERENCES {data}(data_id) ON DELETE CASCADE,
    source        TEXT NOT NULL DEFAULT 'perso',
    source_url    TEXT DEFAULT '',
    is_from_share INTEGER NOT NULL DEFAULT 0,
    is_it_shared  INTEGER NOT NULL DEFAULT 0,
    can_be_shared TEXT NOT NULL DEFAULT 'non',
    shared_at     TEXT DEFAULT NULL,
    created_at    TEXT DEFAULT (datetime('now'))
);
""")
            self.conn.execute("""
                INSERT OR IGNORE INTO global_local_data_type(code, description)
                VALUES (?, ?)""", (type_, description or type_))
            self.conn.commit()
        return {"status": "ok", "type": type_}

    def list_data_types(self) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            "SELECT data_type_id, code, description, active, created_at "
            "FROM global_local_data_type ORDER BY code"))

    def catalogue_exists(self, type_: str) -> bool:
        return self._exists_table(f"{type_}_data")

    def delete_type(self, type_: str, token: str = "") -> Dict[str, Any]:
        """Supprime un type complet (tables + registre). RESET voulu."""
        self._check_write(token)
        self._check_reserved(type_)
        data, tag, tag_type, sharing = self._tables_for(type_)
        with self._lock:
            for t in (data, tag, tag_type, sharing):
                if self._exists_table(t):
                    self.conn.execute(f"DROP TABLE IF EXISTS {t}")
            self.conn.execute(
                "DELETE FROM global_local_data_type WHERE code = ?", (type_,))
            self.conn.commit()
        return {"status": "ok", "type": type_}

    def activate_type(self, type_: str, active: bool, token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        with self._lock:
            self.conn.execute(
                "UPDATE global_local_data_type SET active = ?, "
                "updated_at = datetime('now') WHERE code = ?",
                (1 if active else 0, type_))
            self.conn.commit()
        return {"status": "ok", "type": type_, "active": active}

    # ── Data (CRUD par ref ou data_id) ──────────────────────

    def _row_columns_for(self, type_: str, dvt: str) -> List[str]:
        """Colonnes data_value_* existantes pour un row de {type}_data."""
        cols = {r[1] for r in self.conn.execute(
            f"PRAGMA table_info({type_}_data)").fetchall()}
        return sorted(c for c in cols if c.startswith("data_value_"))

    def _ensure_row_columns(self, type_: str, dvt: str, token: str) -> None:
        """Crée les colonnes data_value_* manquantes (row composé)."""
        parsed = _parse_row_type(dvt)
        if not parsed:
            return
        cols = {r[1] for r in self.conn.execute(
            f"PRAGMA table_info({type_}_data)").fetchall()}
        for header, typ in parsed:
            col = f"data_value_{header}"
            if col in cols:
                continue
            self.conn.execute(
                f"ALTER TABLE {type_}_data ADD COLUMN {col} {_col_type_for(typ)}")

    def add_row_column(self, type_: str, header: str, value_type: str,
                       token: str = "") -> Dict[str, Any]:
        """add_colonne : étend un type row avec un nouveau header."""
        self._check_write(token)
        self._check_type(type_)
        if not header or not header.replace("_", "").isalnum():
            raise ValueError(f"header invalide: {header!r}")
        col = f"data_value_{header}"
        with self._lock:
            if col in {r[1] for r in self.conn.execute(
                    f"PRAGMA table_info({type_}_data)").fetchall()}:
                return {"status": "exists", "column": col}
            self.conn.execute(
                f"ALTER TABLE {type_}_data ADD COLUMN {col} {_col_type_for(value_type)}")
            self.conn.commit()
        return {"status": "ok", "column": col}

    @staticmethod
    def _coerce_value(dvt: str, value: Any):
        """Normalise une valeur selon son data_value_type (best-effort)."""
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
        return str(value)  # string, text[n]ch

    def upsert(self, type_: str, ref: str, name: str = "", namespace: str = "",
               version: str = "latest", data_value_type: str = "json",
               value: Any = "{}", ref_file: str = "", path: str = "",
               description: str = "", status: str = "active",
               row: Optional[Dict[str, Any]] = None,
               last_modify: Optional[str] = None,
               token: str = "") -> Dict[str, Any]:
        """add/modify d'une data (upsert). row : values des headers (row type)."""
        self._check_write(token)
        self._check_type(type_)
        if not ref:
            raise ValueError("ref requis")
        self._check_reserved(ref)
        dvt = data_value_type.strip()
        if dvt not in SCALAR_VALUE_TYPES and dvt not in EXTERNAL_VALUE_TYPES \
                and not dvt.startswith("row(") and not dvt.startswith("text["):
            raise ValueError(f"data_value_type inconnu: {dvt!r}")
        if dvt == "file" and not ref_file:
            raise ValueError("data_value_type=file exige ref_file")
        did = data_id_of(ref)
        parsed = _parse_row_type(dvt)
        with self._lock:
            self._ensure_row_columns(type_, dvt, token)
            if parsed:
                value = json.dumps(row or {})
                vals_rows = {}
                for header, _typ in parsed:
                    rkey = header
                    if rkey in (row or {}):
                        vals_rows[f"data_value_{header}"] = self._coerce_value(_typ, row[rkey])
                    elif f"data_value_{header}" in (row or {}):
                        vals_rows[f"data_value_{header}"] = self._coerce_value(
                            _typ, row[f"data_value_{header}"])
            else:
                vals_rows = {}
                if not isinstance(value, str):
                    value = json.dumps(value) if dvt in ("json", "row") else str(value)
                if dvt != "json" and dvt not in ("string",) and not dvt.startswith("text["):
                    value = self._coerce_value(dvt, value)
                else:
                    value = str(value) if dvt != "json" else value
            if last_modify:
                now = last_modify
            else:
                now = time.strftime("%Y-%m-%d %H:%M:%S")
            cols = ["data_id", "ref", "name", "namespace", "version",
                    "data_value_type", "value", "ref_file", "path",
                    "description", "status", "last_modify"]
            params = [did, ref, name or ref, namespace, version, dvt,
                      value, ref_file, path, description, status, now]
            old = self.conn.execute(
                f"SELECT data_id FROM {type_}_data WHERE ref = ?", (ref,)).fetchone()
            if old:
                sets = ", ".join(f"{c}=?" for c in cols[2:])
                sets += ", updated_at=?"
                self.conn.execute(
                    f"UPDATE {type_}_data SET {sets} WHERE ref = ?",
                    params[2:] + [ref])
            else:
                ph = ",".join("?" * len(params))
                self.conn.execute(
                    f"INSERT INTO {type_}_data ({','.join(cols)}) VALUES ({ph})",
                    params)
            if vals_rows:
                for col, v in vals_rows.items():
                    self.conn.execute(
                        f"UPDATE {type_}_data SET {col} = ? WHERE data_id = ?",
                        (v, did))
            self.conn.commit()
        return {"ok": True, "type": type_, "data_id": did, "ref": ref}

    def delete(self, type_: str, ref: str = "", data_id: Optional[int] = None,
               token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_type(type_)
        did = data_id if data_id is not None else data_id_of(ref)
        with self._lock:
            cur = self.conn.execute(
                f"DELETE FROM {type_}_data WHERE data_id = ?", (did,))
            self.conn.commit()
        return {"ok": True, "deleted": cur.rowcount, "data_id": did}

    def _entry(self, type_: str, ref: str, data_id: Optional[int] = None
               ) -> Optional[Dict[str, Any]]:
        if data_id is not None:
            r = self.conn.execute(
                f"SELECT * FROM {type_}_data WHERE data_id = ?", (data_id,)).fetchone()
        else:
            r = self.conn.execute(
                f"SELECT * FROM {type_}_data WHERE ref = ?", (ref,)).fetchone()
        return _row_to_dict(r) if r else None

    def add_last_modify_col(self, type_: str) -> None:
        _add_column_if_missing(self.conn, f"{type_}_data", "last_modify", "TEXT")

    def get(self, type_: str, ref: str = "", data_id: Optional[int] = None,
            resolve_value: bool = True) -> Dict[str, Any]:
        """get par ref ou data_id. row → format plat {row: {header: value, …}}."""
        self._check_type(type_)
        e = self._entry(type_, ref, data_id)
        if not e:
            raise CatalogueTypeNotFound(f"data introuvable: {type_}/{ref or data_id}")
        dvt = e.get("data_value_type") or "json"
        parsed = _parse_row_type(dvt)
        if parsed:
            row = {}
            for header, _typ in parsed:
                v = e.get(f"data_value_{header}")
                if v is not None:
                    row[header] = v
            e["row"] = row
        rows = self._rows_list(type_, e["data_id"])
        if rows:
            e["tags"] = rows
        sh = self.get_sharing(type_, ref or "", data_id=e["data_id"])
        if sh:
            e["sharing"] = sh
        return e

    def _rows_list(self, type_: str, data_id: int,
                   tag_types: bool = False) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            f"SELECT tag_id, tag_type, tag_value FROM {type_}_tag "
            f"WHERE data_id = ? ORDER BY tag_type, tag_value", (data_id,)))

    def list(self, type_: str, namespace: str = "", page: int = 1,
             page_size: int = 50, sort: str = "name", order: str = "asc",
             status: str = "") -> Dict[str, Any]:
        self._check_type(type_)
        where = []
        args: List[Any] = []
        if namespace:
            where.append("namespace = ?")
            args.append(namespace)
        if status:
            where.append("status = ?")
            args.append(status)
        w = ("WHERE " + " AND ".join(where)) if where else ""
        sort = sort if sort in ("ref", "name", "namespace", "version",
                                "updated_at", "last_modify") else "name"
        order = "DESC" if order.lower() == "desc" else "ASC"
        total = self.conn.execute(
            f"SELECT COUNT(*) c FROM {type_}_data {w}", args).fetchone()["c"]
        off = max(0, (page - 1) * page_size)
        rows = _rows_to_list(self.conn.execute(
            f"SELECT data_id, ref, name, namespace, version, data_value_type, "
            f"status, last_modify FROM {type_}_data {w} "
            f"ORDER BY {sort} {order} LIMIT ? OFFSET ?", args + [page_size, off]))
        return {"total": total, "page": page, "page_size": page_size,
                "items": rows}

    def list_recursive(self, type_: str, namespace: str = "") -> List[Dict[str, Any]]:
        self._check_type(type_)
        if not namespace:
            return _rows_to_list(self.conn.execute(
                f"SELECT data_id, ref, name, namespace, version, status "
                f"FROM {type_}_data ORDER BY namespace, name"))
        return _rows_to_list(self.conn.execute(
            f"SELECT data_id, ref, name, namespace, version, status "
            f"FROM {type_}_data WHERE namespace = ? OR namespace LIKE ? "
            f"ORDER BY namespace, name", (namespace, namespace + "/%")))

    def search(self, type_: str, q: str = "", tag_type: str = "",
               tag_value: str = "") -> List[Dict[str, Any]]:
        self._check_type(type_)
        if tag_type:
            w = "JOIN {t}_tag tg ON tg.data_id = d.data_id WHERE tg.tag_type = ?"
            args = [tag_type]
            if tag_value is not None:
                w += " AND tg.tag_value = ?"
                args.append(str(tag_value))
            rows = _rows_to_list(self.conn.execute(
                f"SELECT DISTINCT d.data_id, d.ref, d.name, d.namespace, "
                f"d.version, d.status FROM {type_}_data d " + w,
                args))
        else:
            w = ""
            args: List[Any] = []
            if q:
                w = "WHERE d.name LIKE ? OR d.ref LIKE ? OR d.description LIKE ?"
                args = [f"%{q}%"] * 3
            rows = _rows_to_list(self.conn.execute(
                f"SELECT d.data_id, d.ref, d.name, d.namespace, d.version, "
                f"d.status FROM {type_}_data d " + w +
                (" ORDER BY d.name" if q else ""), args))
        return rows

    def refresh(self, type_: str, data_id: int,
                last_access: str = "") -> Dict[str, Any]:
        """Refresh conditionnel : data si last_modify > last_access, sinon
        {"modified": False} (sémantique 304). Aucune écriture."""

        self._check_type(type_)
        e = self.conn.execute(
            f"SELECT data_id, ref, last_modify FROM {type_}_data "
            f"WHERE data_id = ?", (data_id,)).fetchone()
        if not e:
            raise CatalogueTypeNotFound(f"data introuvable: {type_}/{data_id}")
        if last_access and e["last_modify"] and str(e["last_modify"]) <= str(last_access):
            return {"modified": False, "data_id": data_id, "ref": e["ref"]}
        return {"modified": True, "data_id": data_id, "ref": e["ref"],
                "last_modify": e["last_modify"]}

    # ── Tags ────────────────────────────────────────────────

    def tag_type_add(self, type_: str, tag_type: str, tag_value_type: str = "text",
                     description: str = "", token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_type(type_)
        if tag_value_type not in TAG_VALUE_TYPES:
            raise ValueError(f"tag_value_type inconnu: {tag_value_type!r}")
        with self._lock:
            self.conn.execute(
                f"INSERT OR IGNORE INTO {type_}_tag_type "
                f"(tag_type, tag_value_type, description) VALUES (?, ?, ?)",
                (tag_type, tag_value_type, description))
            self.conn.execute(
                f"UPDATE {type_}_tag_type SET tag_value_type = ?, description = ? "
                f"WHERE tag_type = ?", (tag_value_type, description, tag_type))
            self.conn.commit()
        return {"ok": True, "type": type_, "tag_type": tag_type,
                "tag_value_type": tag_value_type}

    def tag_type_delete(self, type_: str, tag_type: str, token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        with self._lock:
            self.conn.execute(f"DELETE FROM {type_}_tag_type WHERE tag_type = ?",
                              (tag_type,))
            self.conn.execute(f"DELETE FROM {type_}_tag WHERE tag_type = ?",
                              (tag_type,))
            self.conn.commit()
        return {"ok": True, "tag_type": tag_type}

    def list_tag_types(self, type_: str) -> List[Dict[str, Any]]:
        self._check_type(type_)
        return _rows_to_list(self.conn.execute(
            f"SELECT tag_type, tag_value_type, description FROM {type_}_tag_type "
            f"ORDER BY tag_type"))

    def tag_attach(self, type_: str, ref: str = "", data_id: Optional[int] = None,
                   tag_type: str = "", tag_value: Any = None,
                   token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_type(type_)
        if not tag_type or tag_value is None:
            raise ValueError("tag_type + tag_value requis")
        tt = self.conn.execute(
            f"SELECT tag_value_type FROM {type_}_tag_type WHERE tag_type = ?",
            (tag_type,)).fetchone()
        if not tt:
            raise ValueError(f"tag_type non déclaré: {tag_type!r} "
                             f"(tag_type_add d'abord)")
        tv = str(tag_value)
        if isinstance(tag_value, (list, dict)):
            tv = json.dumps(tag_value)
        did = data_id if data_id is not None else data_id_of(ref)
        with self._lock:
            self.conn.execute(
                f"INSERT OR IGNORE INTO {type_}_tag "
                f"(data_id, tag_type, tag_value) VALUES (?, ?, ?)",
                (did, tag_type, tv))
            self.conn.commit()
        return {"ok": True, "type": type_, "data_id": did, "tag_type": tag_type,
                "tag_value": tv}

    def tag_detach(self, type_: str, ref: str = "", data_id: Optional[int] = None,
                   tag_type: str = "", tag_value: Any = None,
                   token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        did = data_id if data_id is not None else data_id_of(ref)
        with self._lock:
            if tag_value is None:
                self.conn.execute(
                    f"DELETE FROM {type_}_tag WHERE data_id = ? AND tag_type = ?",
                    (did, tag_type))
            else:
                self.conn.execute(
                    f"DELETE FROM {type_}_tag WHERE data_id = ? AND tag_type = ? "
                    f"AND tag_value = ?", (did, tag_type, str(tag_value)))
            self.conn.commit()
        return {"ok": True, "data_id": did}

    def tags_by_value(self, type_: str, tag_type: str,
                      tag_value: Any = None) -> List[Dict[str, Any]]:
        self._check_type(type_)
        if tag_value is None:
            return _rows_to_list(self.conn.execute(
                f"SELECT tg.data_id, d.ref, d.name, tg.tag_type, tg.tag_value "
                f"FROM {type_}_tag tg JOIN {type_}_data d "
                f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? "
                f"ORDER BY tg.tag_value", (tag_type,)))
        return _rows_to_list(self.conn.execute(
            f"SELECT tg.data_id, d.ref, d.name, tg.tag_type, tg.tag_value "
            f"FROM {type_}_tag tg JOIN {type_}_data d "
            f"ON d.data_id = tg.data_id WHERE tg.tag_type = ? AND tg.tag_value = ? "
            f"ORDER BY d.name", (tag_type, str(tag_value))))

    # ── Partage ─────────────────────────────────────────────

    def set_sharing(self, type_: str, ref: str = "",
                    data_id: Optional[int] = None,
                    source: str = "perso", source_url: str = "",
                    is_from_share: bool = False, is_it_shared: bool = False,
                    can_be_shared: str = "non", token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_type(type_)
        if can_be_shared not in SHARING_LEVELS:
            raise ValueError(f"can_be_shared invalide: {can_be_shared!r}")
        did = data_id if data_id is not None else data_id_of(ref)
        with self._lock:
            self.conn.execute(
                f"INSERT INTO {type_}_source_and_sharing "
                f"(data_id, source, source_url, is_from_share, is_it_shared, "
                f"can_be_shared, shared_at) VALUES (?, ?, ?, ?, ?, ?, "
                f"datetime('now')) "
                f"ON CONFLICT(data_id) DO UPDATE SET source=excluded.source, "
                f"source_url=excluded.source_url, "
                f"is_from_share=excluded.is_from_share, "
                f"is_it_shared=excluded.is_it_shared, "
                f"can_be_shared=excluded.can_be_shared, "
                f"shared_at=datetime('now')",
                (did, source, source_url, 1 if is_from_share else 0,
                 1 if is_it_shared else 0, can_be_shared))
            self.conn.commit()
        return {"ok": True, "data_id": did}

    def get_sharing(self, type_: str, ref: str = "",
                    data_id: Optional[int] = None) -> Optional[Dict[str, Any]]:
        self._check_type(type_)
        did = data_id if data_id is not None else data_id_of(ref)
        return _row_to_dict(self.conn.execute(
            f"SELECT * FROM {type_}_source_and_sharing WHERE data_id = ?",
            (did,)).fetchone())

    def set_shared_default(self, type_: str, tag_type: str = "*",
                           tag_value: str = "*", can_be_shared: str = "non",
                           token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        if can_be_shared not in SHARING_LEVELS:
            raise ValueError(f"can_be_shared invalide: {can_be_shared!r}")
        dtr = self.conn.execute(
            "SELECT data_type_id FROM global_local_data_type WHERE code = ?",
            (type_,)).fetchone()
        if not dtr:
            raise CatalogueTypeNotFound(f"type inconnu: {type_}")
        with self._lock:
            self.conn.execute(
                "INSERT INTO global_local_shared_default "
                "(data_type_id, tag_type, tag_value, can_be_shared) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(data_type_id, tag_type, tag_value) DO UPDATE SET "
                "can_be_shared = excluded.can_be_shared, "
                "updated_at = datetime('now')",
                (dtr["data_type_id"], tag_type, tag_value, can_be_shared))
            self.conn.commit()
        return {"ok": True, "type": type_}

    def resolve_can_be_shared(self, type_: str, ref: str = "",
                              data_id: Optional[int] = None,
                              tag_type: str = "", tag_value: str = "") -> str:
        """Résolution cascade : (type, tag_type, tag_value) → (type,'*','*')
        → défaut 'non'."""
        self._check_type(type_)
        dtr = self.conn.execute(
            "SELECT data_type_id FROM global_local_data_type WHERE code = ?",
            (type_,)).fetchone()
        if not dtr:
            return "non"
        dtid = dtr["data_type_id"]
        for tt, tv in ((tag_type, tag_value), ("*", "*")):
            r = self.conn.execute(
                "SELECT can_be_shared FROM global_local_shared_default "
                "WHERE data_type_id = ? AND tag_type = ? AND tag_value = ?",
                (dtid, tt or "*", tv or "*")).fetchone()
            if r:
                return r["can_be_shared"]
        sh = self.get_sharing(type_, ref, data_id)
        return (sh["can_be_shared"] if sh and sh["can_be_shared"] != "non"
                else "non")

    def list_shared_default(self) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            "SELECT sd.*, dt.code AS data_type "
            "FROM global_local_shared_default sd "
            "JOIN global_local_data_type dt ON dt.data_type_id = sd.data_type_id "
            "ORDER BY dt.code, sd.tag_type, sd.tag_value"))

    # ── Namespaces ──────────────────────────────────────────

    def create_namespace(self, ns: str, parent: Optional[str] = None,
                         description: str = "", token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_reserved(ns)
        if "/" in ns and not parent:
            parent = ns.rsplit("/", 1)[0]
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO global_local_namespace "
                "(ns, parent, description) VALUES (?, ?, ?)",
                (ns, parent, description))
            self.conn.commit()
        return {"status": "ok", "ns": ns, "parent": parent}

    def get_namespace(self, ns: str) -> Optional[Dict[str, Any]]:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM global_local_namespace WHERE ns = ?", (ns,)).fetchone())

    def list_namespaces(self, parent: Optional[str] = None) -> List[Dict[str, Any]]:
        if parent is None:
            return _rows_to_list(self.conn.execute(
                "SELECT * FROM global_local_namespace ORDER BY ns"))
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM global_local_namespace WHERE parent = ? ORDER BY ns",
            (parent,)))

    def list_namespaces_recursive(self, prefix: str = "") -> List[Dict[str, Any]]:
        if not prefix:
            return _rows_to_list(self.conn.execute(
                "SELECT * FROM global_local_namespace ORDER BY ns"))
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM global_local_namespace WHERE ns = ? OR ns LIKE ? "
            "ORDER BY ns", (prefix, prefix + "/%")))

    def delete_namespace(self, ns: str, token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        self._check_reserved(ns)
        with self._lock:
            self.conn.execute(
                "DELETE FROM global_local_namespace WHERE ns = ? OR ns LIKE ?",
                (ns, ns + "/%"))
            self.conn.commit()
        return {"ok": True, "ns": ns}

    # ── Paths ───────────────────────────────────────────────

    def create_path(self, path_name: str, address: str,
                    scheme: str = "file", description: str = "",
                    token: str = "") -> Dict[str, Any]:
        self._check_write(token)
        if path_name.startswith("/$") or path_name.startswith("$"):
            raise ValueError("path ne commence jamais par /$ ou $")
        if "*" in path_name:
            raise ValueError("les écritures de chemin interdisent *")
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO global_local_path "
                "(path_name, address, scheme, description) VALUES (?, ?, ?, ?)",
                (path_name, address, scheme, description))
            self.conn.commit()
        return {"ok": True, "path_name": path_name}

    def list_paths(self, scheme: str = "") -> List[Dict[str, Any]]:
        if scheme:
            return _rows_to_list(self.conn.execute(
                "SELECT * FROM global_local_path WHERE scheme = ? "
                "ORDER BY path_name", (scheme,)))
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM global_local_path ORDER BY path_name"))

    def resolve_path(self, target: str) -> Dict[str, Any]:
        """Résolution par précision de gauche à droite (/lib/a/$1 prime)."""
        parts = target.split("/")
        best = None
        best_len = -1
        for row in self.conn.execute(
                "SELECT path_name, address, scheme FROM global_local_path "
                "ORDER BY LENGTH(path_name) DESC").fetchall():
            pn = row["path_name"].split("/")
            if len(pn) > len(parts):
                continue
            score = 0
            ok = True
            for i, seg in enumerate(pn):
                if seg[0] == "$":
                    continue
                if seg != parts[i]:
                    ok = False
                    break
                score += 1
            if ok and score > best_len:
                best = row
                best_len = score
        if not best:
            return {"resolved": False, "target": target, "address": target,
                    "scheme": "file"}
        addr = best["address"].replace("/$", "")  # substitution $N
        for i, seg in enumerate(parts[len(best["path_name"].split("/")):]):
            addr += "/" + seg
        return {"resolved": True, "target": target, "address": addr,
                "scheme": best["scheme"]}

    # ── Buffer (écritures externes) ─────────────────────────

    def buffer_push(self, ops: List[Dict[str, Any]],
                    external_tag: str = "", token: str = "") -> Dict[str, Any]:
        """Dépose N ops 'pending' (importeur externe). UNE écriture batch."""
        self._check_write(token)
        if not ops:
            return {"ok": True, "pushed": 0}
        with self._lock:
            self.conn.executemany(
                "INSERT INTO global_local_buffer_op "
                "(direction, domain, op, payload_json, status, external_tag, "
                "ref_external) VALUES ('in', ?, ?, ?, 'pending', ?, ?)",
                [(o.get("domain", ""), o.get("op", "add"),
                  json.dumps(o.get("payload", {})), external_tag,
                  o.get("ref_external", "")) for o in ops])
            self.conn.commit()
        return {"ok": True, "pushed": len(ops)}

    def buffer_process(self, token: str = "",
                       limit: int = 500) -> Dict[str, Any]:
        """Consumer du writer : applique les ops pending en mini-batchs."""
        self._check_write(token)
        applied = errors = 0
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM global_local_buffer_op WHERE status = 'pending' "
                "ORDER BY op_id LIMIT ?", (limit,)).fetchall()
            for row in rows:
                op_id = row["op_id"]
                try:
                    payload = json.loads(row["payload_json"] or "{}")
                    if row["direction"] == "in":
                        self._apply_op(row["domain"], row["op"], payload)
                    self.conn.execute(
                        "UPDATE global_local_buffer_op SET status='applied', "
                        "applied_at=datetime('now') WHERE op_id = ?", (op_id,))
                    applied += 1
                except Exception as e:  # jamais bloquant
                    self.conn.execute(
                        "UPDATE global_local_buffer_op SET status='error', "
                        "error=? WHERE op_id = ?", (str(e)[:500], op_id))
                    errors += 1
            self.conn.commit()
        return {"ok": True, "applied": applied, "errors": errors,
                "pending_left": self._buffer_count("pending")}

    def _apply_op(self, domain: str, op: str, payload: Dict[str, Any]) -> None:
        if op == "delete":
            self.conn.execute(
                f"DELETE FROM {domain}_data WHERE data_id = ?",
                (payload.get("data_id") or data_id_of(payload.get("ref", "")),))
            return
        self._upsert_direct(domain, payload)

    def _upsert_direct(self, type_: str, p: Dict[str, Any]) -> None:
        if not self._exists_table(f"{type_}_data"):
            raise CatalogueTypeNotFound(f"type inconnu: {type_}")
        ref = p.get("ref", "")
        if not ref:
            raise ValueError("ref requis")
        dvt = p.get("data_value_type", "json")
        did = data_id_of(ref)
        if dvt.startswith("row("):
            val = json.dumps(p.get("row") or {})
        else:
            v = p.get("value", "{}")
            val = json.dumps(v) if not isinstance(v, str) else v
        cols = ["data_id", "ref", "name", "namespace", "version",
                "data_value_type", "value", "ref_file", "path", "description",
                "status", "last_modify"]
        params = [did, ref, p.get("name") or ref, p.get("namespace", ""),
                  p.get("version", "latest"), dvt, val,
                  p.get("ref_file", ""), p.get("path", ""),
                  p.get("description", ""), p.get("status", "active"),
                  "datetime('now')"]
        old = self.conn.execute(
            f"SELECT data_id FROM {type_}_data WHERE ref = ?", (ref,)).fetchone()
        if old:
            sets = ", ".join(f"{c}=?" for c in cols[2:])
            self.conn.execute(
                f"UPDATE {type_}_data SET {sets} WHERE ref = ?",
                params[2:] + [ref])
        else:
            ph = ",".join("?" * len(params))
            self.conn.execute(
                f"INSERT INTO {type_}_data ({','.join(cols)}) VALUES ({ph})",
                params)

    def _buffer_count(self, status: str) -> int:
        return self.conn.execute(
            "SELECT COUNT(*) c FROM global_local_buffer_op WHERE status = ?",
            (status,)).fetchone()["c"]

    def buffer_status(self, external_tag: str = "",
                      status: str = "") -> List[Dict[str, Any]]:
        w, args = [], []
        if external_tag:
            w.append("external_tag = ?")
            args.append(external_tag)
        if status:
            w.append("status = ?")
            args.append(status)
        where = ("WHERE " + " AND ".join(w)) if w else ""
        return _rows_to_list(self.conn.execute(
            f"SELECT op_id, direction, domain, op, status, error, external_tag, "
            f"ref_external, created_at, applied_at "
            f"FROM global_local_buffer_op {where} ORDER BY op_id DESC LIMIT 500",
            args))

    def buffer_retry(self, token: str = "", limit: int = 500) -> Dict[str, Any]:
        """Rejoue les ops en erreur (après correction) — jamais bloquant."""
        self._check_write(token)
        with self._lock:
            self.conn.execute(
                "UPDATE global_local_buffer_op SET status='pending', "
                "error='' WHERE status='error' LIMIT ?", (limit,))
            self.conn.commit()
        return self.buffer_process(token=token, limit=limit)

    # ── Cleanup ─────────────────────────────────────────────

    def cleanup_prefix(self, prefix: str, token: str = "") -> Dict[str, Any]:
        """Supprime les data/réfs d'un préfixe (réservé : auto-test-*)."""
        self._check_write(token)
        if not prefix.startswith(RESERVED_TEST_PREFIX):
            raise ValueError(
                f"cleanup réservé aux espaces de test: {RESERVED_TEST_PREFIX}*")
        removed = 0
        with self._lock:
            for dt in self.list_data_types():
                t = f"{dt['code']}_data"
                if not self._exists_table(t):
                    continue
                cur = self.conn.execute(
                    f"DELETE FROM {t} WHERE ref LIKE ?", (prefix + "%",))
                removed += cur.rowcount
            self.conn.execute(
                "DELETE FROM global_local_namespace WHERE ns LIKE ?",
                (prefix + "%",))
            self.conn.commit()
        return {"ok": True, "removed": removed}

    # ── Privileges (famille SECURITY) ───────────────────────

    def create_privilege(self, chemin_ref: str, kind: str = "path",
                         agent_id: int = -1, team: int = -1,
                         level: int = 1, read: str = "----",
                         write: str = "----", exec: str = "----",
                         privileged: str = "----", ask: str = "none",
                         deadline: Optional[str] = None,
                         description: str = "",
                         token: str = "") -> Dict[str, Any]:
        """Crée une autorisation (writer privé)."""
        self._check_write(token)
        if ask not in ASK_LEVELS:
            raise ValueError(f"ask invalide: {ask!r}")
        mode = lambda s: (s or "----").ljust(4, "-")[:4]
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO global_local_privilege "
                "(chemin_ref, kind, agent_id, team, level, read, write, exec, "
                "privileged, ask, deadline, description) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chemin_ref, kind, agent_id, team, level, mode(read),
                 mode(write), mode(exec), mode(privileged), ask, deadline,
                 description))
            self.conn.commit()
        return {"status": "ok", "id_auth": cur.lastrowid, "chemin_ref": chemin_ref}

    def list_privileges(self, kind: str = "") -> List[Dict[str, Any]]:
        if kind:
            return _rows_to_list(self.conn.execute(
                "SELECT * FROM global_local_privilege WHERE kind = ? "
                "ORDER BY chemin_ref", (kind,)))
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM global_local_privilege ORDER BY chemin_ref"))

    def _conditions_for(self, id_auth: int) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM global_local_privilege_condition WHERE id_auth = ?",
            (id_auth,)))

    def resolve_privilege(self, chemin: str, kind: str = "path",
                          agent_id: int = -1, team: int = -1,
                          level: int = 1, op: str = "read") -> List[Dict[str, Any]]:
        """Match par précision (V1) : ref/path/cmd, exclusion de préfixes,
        filtres d'identité (agent_id/team : ciblés ou -1)."""
        sql = "SELECT * FROM global_local_privilege WHERE kind = ? "
        params: List[Any] = [kind]
        if int(agent_id) != -1:
            sql += "AND (agent_id = ? OR agent_id = -1) "
            params.append(int(agent_id))
        if int(team) != -1:
            sql += "AND (team = ? OR team = -1) "
            params.append(int(team))
        sql += "ORDER BY LENGTH(chemin_ref) DESC"
        rows = self.conn.execute(sql, params).fetchall()
        out = []
        for r in rows:
            if self._matches(r, chemin):
                out.append(dict(r))
        return out

    def _matches(self, row: sqlite3.Row, chemin: str) -> bool:
        if row["kind"] == "cmd":
            target = (chemin or "").strip()
            declared = (row["chemin_ref"] or "").strip()
            return target == declared or (declared and
                                          target.startswith(declared + " "))
        if row["kind"] == "path":
            target = (chemin or "").strip("/")
            declared = (row["chemin_ref"] or "").strip("/")
            return target == declared or target.startswith(declared + "/")
        # ref
        return chemin == row["chemin_ref"]

    def _resolve_allowed(self, chemin: str, level: str = "agent",
                         op: str = "read", agent_id: int = -1,
                         team: int = -1, kind: str = "path") -> Tuple[bool, str, Optional[Dict[str, Any]]]:
        """(allowed, ask, row du meilleur match accordant) — SANS consommation.

        ask séparé du droit : un row qui accorde le mode mais porte
        ask != none → droit accordé + demande requise (ask_pending)."""
        chars = {"agent": 3, "agent_with_root": 2, "humain": 1,
                 "humain_with_root": 0}
        idx = chars.get(level, 3)
        pending = None
        for r in self.resolve_privilege(chemin, kind=kind,
                                        agent_id=agent_id, team=team):
            if op in ("read", "write", "exec", "privileged"):
                if (r[op] or "----")[idx: idx + 1] == "-":
                    continue
            a = r["ask"] or "none"
            if a == "none":
                return True, "none", dict(r)
            if pending is None:
                pending = dict(r)
        if pending is not None:
            return True, pending.get("ask") or "none", pending
        return False, "none", None

    def check_privilege_dict(self, chemin: str, level: str = "agent",
                             op: str = "read", agent_id: int = -1,
                             team: int = -1, kind: str = "path") -> Dict[str, Any]:
        """Vérifie SANS consommer (route priv/check) — contrat {allowed, ask,
        ask_pending}."""
        allowed, ask, row = self._resolve_allowed(
            chemin, level=level, op=op, agent_id=agent_id, team=team, kind=kind)
        out = {"status": "ok", "allowed": bool(allowed), "ask": ask,
               "ask_pending": bool(allowed) and ask not in ("none",)}
        if row:
            out["id_auth"] = row["id_auth"]
            out["chemin_ref"] = row["chemin_ref"]
        return out

    def check_privilege(self, chemin: str, level: str = "agent",
                        op: str = "read", agent_id: int = -1,
                        team: int = -1, kind: str = "path") -> bool:
        """Vrai si `level` a le privilège `op` sur `chemin` (ask none)."""
        allowed, _ask, _row = self._resolve_allowed(
            chemin, level=level, op=op, agent_id=agent_id, team=team, kind=kind)
        return bool(allowed)

    def use_privilege(self, chemin: str, level: str = "agent", op: str = "read",
                      agent_id: int = -1, team: int = -1, kind: str = "path",
                      token: str = "") -> Dict[str, Any]:
        """Vérifie + CONSOMME (nb_times/lastcall) — writer privé.

        Retourne {status, allowed, ask, ask_pending, id_auth, chemin_ref,
        consumed, renewal_request} (contrat V1)."""
        self._check_write(token)
        allowed, ask, row = self._resolve_allowed(chemin, level=level, op=op,
                                                  agent_id=agent_id, team=team,
                                                  kind=kind)
        if not allowed:
            return {"status": "ok", "allowed": False, "ask": ask,
                    "ask_pending": False, "consumed": 0,
                    "renewal_request": None}
        consumed = 0
        renewal_request = None
        with self._lock:
            conds = self.conn.execute(
                "SELECT * FROM global_local_privilege_condition "
                "WHERE id_auth = ?", (row["id_auth"],)).fetchall()
            for c in conds:
                if c["condition_type"] == "nb_times" and c["compteur"] is not None:
                    if c["compteur"] <= 0:
                        return {"status": "ok", "allowed": False, "ask": ask,
                                "ask_pending": False, "consumed": 0,
                                "renewal_request": {
                                    "reason": "limite d'usage épuisée",
                                    "chemin_ref": row["chemin_ref"],
                                    "expired_id_auth": row["id_auth"]}}
                    self.conn.execute(
                        "UPDATE global_local_privilege_condition SET compteur = ? "
                        "WHERE condition_id = ?", (c["compteur"] - 1, c["condition_id"]))
                    consumed += 1
                elif c["condition_type"] == "lastcall" and c["valeur"]:
                    self.conn.execute(
                        "UPDATE global_local_privilege_condition SET "
                        "last_use_at = datetime('now') WHERE condition_id = ?",
                        (c["condition_id"],))
            self.conn.commit()
        return {"status": "ok", "allowed": True, "ask": ask,
                "ask_pending": ask not in ("none",),
                "id_auth": row["id_auth"], "chemin_ref": row["chemin_ref"],
                "consumed": consumed, "renewal_request": renewal_request}

    def approve_privilege(self, decider_agent_id: int, decider_level: str,
                          chemin: str = "", kind: str = "path",
                          token: str = "") -> Dict[str, Any]:
        """Approbation d'une demande ask=security_supervisor."""
        self._check_write(token)
        sup = self.conn.execute(
            "SELECT * FROM global_local_security_supervisor WHERE active = 1",
            ()).fetchone()
        if not sup:
            return {"ok": False, "error": "aucun superviseur désigné"}
        return {"ok": True, "supervisor": sup["supervisor_agent_id"]}

    # Batching (submit_batch) — conservé de la V1 pour la compat des
    # importeurs : soumet une liste d'ops déjà traités.
    def submit_batch(self, ops: List[Dict[str, Any]],
                     batch_ref: str = "") -> Dict[str, Any]:
        if self._mode == "ro":
            raise WriteDenied("catalogue_local en lecture seule (mode=ro)")
        return self.buffer_push(ops, external_tag="batch",
                                token=self._write_token)

    def batch_status(self, batch_id: str) -> Optional[Dict[str, Any]]:
        return {"batch_id": batch_id}