#!/usr/bin/env python3
"""base — socle générique des domaines sqlite.

DEUX classes, un seul contrat :

  Db    : UN DOMAINE (une connexion, un fichier .db).
          Porte la mécanique : chemin, mode ro|w, PRAGMAs (WAL, busy_timeout,
          foreign_keys), verrou réentrant, mkdir parent, token du
          dedicated_writer, ensure_schema versionné.

  Table : UNE TABLE du domaine. CRUD générique (introspection PRAGMA
          table_info au premier accès), jointure égale simple, et `sql()`
          (escape hatch) pour les opérations tordues — qui doivent vivre
          dans les fichiers spécialisés du domaine, jamais ailleurs.

REGLES DE LA REFONTE (cf. modules/sql_old pour le code historique) :
  1. Tout accès SQLite passe par sqlite/<domaine>/read.py|write.py.
     Aucun autre module n'ouvre de connexion ni n'écrit de requête.
  2. Retours simples (dict / list[dict] / int / None) + exceptions typées
     (WriteDenied, ValidationError). La couche HTTP traduit en réponses.
  3. Le lock d'écriture est INTÉRIEUR à ce module : les appelants n'ont
     jamais à poser de verrou.
  4. Le contrat {status/ok, ...} des routes appartient aux handlers ;
     ici on renvoie des valeurs, pas des enveloppes.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Union


# ── Exceptions (contrat d'erreur commun) ──────────────────────────────────

class SqliteError(Exception):
    """Base des erreurs du module sqlite."""


class WriteDenied(SqliteError):
    """Domaine en lecture seule, ou token writer invalide."""


class ValidationError(SqliteError):
    """Paramètres invalides (colonne inconnue, données mal formées...)."""


# ── Db : un domaine ────────────────────────────────────────────────────────

class Db:
    """Un domaine = une connexion sur un fichier .db.

    mode="ro" : URI SQLite read-only (PHP : impossible d'écrire, même par
    erreur). mode="w" : rwc (crée le fichier si absent) + PRAGMAs d'écriture.
    write_token : requis pour toute écriture (token du dedicated_writer ou
    token du domaine multi-écrivains).
    """

    def __init__(self, db_path: Union[str, Path], mode: str = "ro",
                 write_token: str = ""):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._write_token = write_token
        self._lock = threading.RLock()
        uri = f"file:{self.db_path}?mode={'ro' if mode == 'ro' else 'rwc'}"
        self._conn = sqlite3.connect(uri, uri=True, timeout=30,
                                     check_same_thread=(mode == "ro"))
        self._conn.row_factory = sqlite3.Row
        self._conn.isolation_level = None  # autocommit : chaque stmt commité
        if mode != "ro":
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA foreign_keys=ON")
        self._tables: Dict[str, "Table"] = {}
        self._cols_cache: Dict[str, List[Dict[str, Any]]] = {}

    # ── accès ──────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def table(self, name: str) -> "Table":
        """Retourne l'accès générique à une table (cache par instance)."""
        t = self._tables.get(name)
        if t is None:
            t = Table(self, name)
            self._tables[name] = t
        return t

    def sql(self, query: str, params: Sequence[Any] = (),
            token: str = "") -> List[Dict[str, Any]]:
        """Escape hatch au niveau domaine (DDL dynamique, ALTER TABLE,
        UPDATE/SELECT conditionnels, sous-requêtes). Le garde-token s'applique
        aux écritures. RÉSERVÉ aux fichiers spécialisés du domaine."""
        stripped = query.lstrip().upper()
        readonly = stripped.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN"))
        if not readonly:
            self.check_write(token)
        with self._lock if not readonly else _null_cm():
            cur = self._conn.execute(query, params)
            if readonly:
                return [dict(r) for r in cur]
            return []

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ── gardes ─────────────────────────────────────────────

    def check_write(self, token: str = "") -> None:
        """Refuse toute écriture si le domaine est ro OU (token configuré ET
        token fourni != token du domaine).

        Pas de token configuré (write_token=="") = domaine OUVERT
        (multi-écrivains, ex. runtime) : les écritures sont libres. Le token
        n'est exigé QUE pour les domaines à dedicated_writer (ex. local)."""
        if self._mode == "ro":
            raise WriteDenied(f"{self.db_path.name} en lecture seule")
        if self._write_token and token != self._write_token:
            raise WriteDenied("token writer invalide")

    def columns(self, table: str) -> List[Dict[str, Any]]:
        """Colonnes de la table (cache). [{'name','type','pk',...}]."""
        if table not in self._cols_cache:
            self._cols_cache[table] = [dict(r) for r in self._conn.execute(
                f"PRAGMA table_info({_quote(table)})")]
        return self._cols_cache[table]

    def column_names(self, table: str) -> List[str]:
        return [c["name"] for c in self.columns(table)]

    def ensure_schema(self, schema_version: int, create_statements: List[str],
                      meta_table: str = "meta",
                      reset_below: Optional[int] = None) -> bool:
        """Applique un schéma en UNE fois, idempotent, versionné.

        - 1re fois : exécute `create_statements` + pose schema_version.
        - Version déjà à jour : ne fait rien.
        - Version plus ancienne (et reset_below défini) : DROP des tables du
          domaine (fournies par l'appelant) puis re-création — le reset de
          schéma est UNE DÉCISION EXPLICITE (principe : pas de migration
          cosmétique, on re-remplit après).

        Retourne True si le schéma a été (re)créé."""
        self.check_write(self._write_token)
        with self._lock:
            cur = self._conn.execute(
                f"SELECT value FROM {_quote(meta_table)} WHERE key='schema_version'"
            ).fetchone() if self._exists(meta_table) else None
            ver = int(cur["value"]) if cur else 0
            if ver == schema_version:
                return False
            if reset_below is not None and ver >= reset_below:
                return False
            for stmt in create_statements:
                # executescript gère à la fois un DDL unique et un script
                # multi-instructions (CREATE + seed INSERT, comme local_schema.sql).
                self._conn.executescript(stmt)
            self._conn.execute(
                f"CREATE TABLE IF NOT EXISTS {_quote(meta_table)} "
                "(key TEXT PRIMARY KEY, value TEXT)")
            self._conn.execute(
                f"INSERT OR REPLACE INTO {_quote(meta_table)} "
                "(key, value) VALUES ('schema_version', ?)",
                (str(schema_version),))
            return True

    # alias public : Db.create(...) = apply du schéma du domaine
    def create(self, schema_version: int, create_statements: List[str],
               meta_table: str = "meta", reset_below: Optional[int] = None) -> bool:
        """Crée/applique le schéma du domaine (idempotent, versionné)."""
        return self.ensure_schema(schema_version, create_statements,
                                  meta_table=meta_table, reset_below=reset_below)

    def _exists(self, table: str) -> bool:
        r = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table,)).fetchone()
        return r is not None

    def in_write(self) -> bool:
        """Context manager des FONCTIONS métier : pose le lock d'écriture.

        Usage dans write.py d'un domaine :
            with domaine.db.in_write():
                ...  # plusieurs opérations = UNE transaction atomique
        """
        return _Txn(self)


class _Txn:
    def __init__(self, db: Db):
        self._db = db

    def __enter__(self):
        self._db.check_write(self._db._write_token)
        self._db._lock.acquire()
        self._db._conn.execute("BEGIN")
        return self._db

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._db._conn.execute("COMMIT")
            else:
                self._db._conn.execute("ROLLBACK")
        finally:
            self._db._lock.release()
        return False


# ── Table : CRUD générique ─────────────────────────────────────────────────

class Table:
    """Opérations classiques sur une table du domaine.

    Toutes les écritures posent le verrou + le garde token ; les lectures
    passent sans verrou (SQLite gère les lecteurs concurrents en WAL).

    Colonnes : vérifiées par introspection au 1er accès — une colonne
    inconnue lève ValidationError (piège les fautes de frappe).
    """

    def __init__(self, db: Db, name: str):
        self._db = db
        self.name = name

    # ── écriture ───────────────────────────────────────────

    def add(self, data: Union[Dict[str, Any], List[Dict[str, Any]]],
            token: str = "") -> int:
        """INSERT d'un dict ou d'une liste (executemany en une transaction).

        Retourne le rowid de la dernière ligne insérée (0 si rien).
        Colonnes absentes du dict → NULL ; colonnes inconnues → erreur."""
        self._db.check_write(token)
        rows = data if isinstance(data, list) else [data]
        if not rows:
            return 0
        cols = _common_cols(self._db, self.name, rows)
        col_sql = ", ".join(_quote(c) for c in cols)
        qmarks = ", ".join("?" * len(cols))
        with self._db._lock:
            if len(rows) == 1:
                cur = self._db._conn.execute(
                    f"INSERT INTO {_quote(self.name)} ({col_sql}) "
                    f"VALUES ({qmarks})",
                    [rows[0].get(c) for c in cols])
                return cur.lastrowid
            cur = self._db._conn.executemany(
                f"INSERT INTO {_quote(self.name)} ({col_sql}) "
                f"VALUES ({qmarks})",
                [[r.get(c) for c in cols] for r in rows])
            return cur.lastrowid

    def upsert(self, data: Union[Dict[str, Any], List[Dict[str, Any]]],
               conflict_cols: List[str], token: str = "") -> None:
        """INSERT ... ON CONFLICT(conflict_cols) DO UPDATE (autres colonnes).

        Deux usages : garder la stabilité d'une clé unique (mtime, hash) ou
        recharger sans dupliquer. update_cols vide → DO NOTHING (tout est
        colonne de conflit : on ne fait qu'empêcher le doublon)."""
        self._db.check_write(token)
        rows = data if isinstance(data, list) else [data]
        if not rows:
            return
        for col in conflict_cols:
            if col not in self._db.column_names(self.name):
                raise ValidationError(f"colonne de conflit inconnue: {col}")
        cols = _common_cols(self._db, self.name, rows)
        update_cols = [c for c in cols if c not in conflict_cols]
        sets = ", ".join(f"{_quote(c)}=excluded.{_quote(c)}"
                         for c in update_cols) or "DO NOTHING"
        do = (f"DO UPDATE SET {sets}" if sets != "DO NOTHING"
              else "DO NOTHING")
        q = (f"INSERT INTO {_quote(self.name)} "
             f"({', '.join(_quote(c) for c in cols)}) "
             f"VALUES ({', '.join('?' * len(cols))}) "
             f"ON CONFLICT({', '.join(_quote(c) for c in conflict_cols)}) {do}")
        with self._db._lock:
            for r in rows:
                self._db._conn.execute(q, [r.get(c) for c in cols])

    def update(self, where: Dict[str, Any], changes: Dict[str, Any],
               token: str = "") -> int:
        """UPDATE ... SET changes WHERE where (= sur toutes les clés).

        Retourne le nombre de lignes modifiées."""
        self._db.check_write(token)
        if not changes:
            return 0
        _check_cols(self._db, self.name, list(changes) + list(where))
        with self._db._lock:
            sets = ", ".join(f"{_quote(c)}=?" for c in changes)
            conds, args = _where_sql(where)
            cur = self._db._conn.execute(
                f"UPDATE {_quote(self.name)} SET {sets} WHERE {conds}",
                list(changes.values()) + args)
            return cur.rowcount

    def remove(self, where: Dict[str, Any], token: str = "") -> int:
        """DELETE WHERE where (= sur toutes les clés). Retourne le nb supprimé."""
        self._db.check_write(token)
        _check_cols(self._db, self.name, list(where))
        with self._db._lock:
            conds, args = _where_sql(where)
            cur = self._db._conn.execute(
                f"DELETE FROM {_quote(self.name)} WHERE {conds}", args)
            return cur.rowcount

    # ── lecture ────────────────────────────────────────────

    def get(self, where: Dict[str, Any], cols: Optional[List[str]] = None,
            order_by: str = "") -> Optional[Dict[str, Any]]:
        """UN enregistrement (None si absent). Surcharger avec order_by
        si plusieurs lignes matchent."""
        rows = self.select(where=where, cols=cols, order_by=order_by, limit=1)
        return rows[0] if rows else None

    def select(self, where: Optional[Dict[str, Any]] = None,
               cols: Optional[List[str]] = None, order_by: str = "",
               limit: Optional[int] = None, offset: int = 0) -> List[Dict[str, Any]]:
        """SELECT cols FROM table [WHERE =] [ORDER BY] [LIMIT] [OFFSET]."""
        if cols:
            _check_cols(self._db, self.name, cols)
        sel = ", ".join(_quote(c) for c in cols) if cols else "*"
        q = f"SELECT {sel} FROM {_quote(self.name)}"
        args: List[Any] = []
        if where:
            conds, args = _where_sql(where)
            q += f" WHERE {conds}"
        if order_by:
            q += f" ORDER BY {order_by}"
        if limit is not None:
            q += f" LIMIT {int(limit)} OFFSET {int(offset)}"
        return [dict(r) for r in self._db._conn.execute(q, args)]

    def count(self, where: Optional[Dict[str, Any]] = None) -> int:
        if where:
            conds, args = _where_sql(where)
            r = self._db._conn.execute(
                f"SELECT COUNT(*) n FROM {_quote(self.name)} WHERE {conds}",
                args).fetchone()
        else:
            r = self._db._conn.execute(
                f"SELECT COUNT(*) n FROM {_quote(self.name)}").fetchone()
        return int(r["n"])

    def select_join(self, right_table: str, on: tuple,  # (col_gauche, col_droite)
                    where: Optional[Dict[str, Any]] = None,
                    cols: Optional[List[str]] = None,
                    order_by: str = "", limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """INNER JOIN égalité : table LEFT JOIN right_table ON a=on[0], b=on[1].

        Pour tout le reste (sous-requêtes, group by...) : `sql()` du domaine."""
        left_col, right_col = on
        _check_cols(self._db, self.name, [left_col])
        _check_cols(self._db, right_table, [right_col])
        sel = (", ".join(_quote(c) for c in cols) if cols
               else f"{_quote(self.name)}.*")
        q = (f"SELECT {sel} FROM {_quote(self.name)} "
             f"JOIN {_quote(right_table)} "
             f"ON {_quote(self.name)}.{_quote(left_col)} "
             f"= {_quote(right_table)}.{_quote(right_col)}")
        args: List[Any] = []
        if where:
            conds, args = _where_sql(where)
            q += f" WHERE {conds}"
        if order_by:
            q += f" ORDER BY {order_by}"
        if limit is not None:
            q += f" LIMIT {int(limit)}"
        return [dict(r) for r in self._db._conn.execute(q, args)]

    def sql(self, query: str, params: Sequence[Any] = (),
            token: str = "") -> List[Dict[str, Any]]:
        """Escape hatch (lecture ou écriture selon la requête).

        RÉSERVÉ aux fichiers spécialisés du domaine (jointures tordues,
        sous-requêtes, UPDATE atomiques conditionnels...). Le garde token
        s'applique si la requête ne commence pas par SELECT/WITH/PRAGMA."""
        stripped = query.lstrip().upper()
        readonly = stripped.startswith(("SELECT", "WITH", "PRAGMA", "EXPLAIN"))
        if not readonly:
            self._db.check_write(token)
        with self._db._lock if not readonly else _null_cm():
            cur = self._db._conn.execute(query, params)
            if readonly:
                return [dict(r) for r in cur]
            return []


# ── helpers privés ─────────────────────────────────────────────────────────

def _quote(name: str) -> str:
    return f'"{name}"'


def _where_sql(where: Dict[str, Any]) -> tuple:
    conds = " AND ".join(f"{_quote(k)}=?" for k in where)
    return conds, list(where.values())


def _check_cols(db: Db, table: str, cols: List[str]) -> None:
    known = db.column_names(table)
    unknown = [c for c in cols if c not in known]
    if unknown:
        raise ValidationError(
            f"colonnes inconnues sur {table}: {unknown} (connues: {known})")


def _common_cols(db: Db, table: str, rows: List[Dict[str, Any]]) -> List[str]:
    """Union des clés des lignes, dans l'ordre du 1er dict (vérifiées)."""
    cols: List[str] = []
    for r in rows:
        for k in r:
            if k not in cols:
                cols.append(k)
    _check_cols(db, table, cols)
    return cols


class _null_cm:
    def __enter__(self):
        return None

    def __exit__(self, *a):
        return False