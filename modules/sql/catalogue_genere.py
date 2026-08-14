"""Catalogue Genere — données GÉNÉRÉES (symbols, pétris, contracts…).

Domaine dédié (catalogue_genere.db) : les data_genere sont CALCULÉES depuis
des dépendances (fichiers/skills/symbols) ; on stocke le résultat + une
empreinte de génération (inputs_hash), jamais les sources.

Hiérarchie de stockage RAM/disque :
  - RAM (mémoire) : les data "chaudes" (accédées récemment) — accès rapide.
  - disque (dure)  : le reste. À la lecture on cherche RAM puis disque, et on
    promeut en RAM (dernier accès). Un flusher régulier pousse les diffs de la
    RAM vers le disque, puis évince les lignes RAM les moins accédées :
      * garde toutes les lignes accédées il y a moins de `ram_min_age_s`
        (5 min par défaut) ;
      * si le nombre de lignes RAM dépasse `ram_max_lines`, supprime les plus
        vieilles (déjà sauvées sur le disque), sans descendre sous
        `ram_max_lines` et sans retirer les data à accès très récent.
  - `disk_max_age_s` : paramètre pour un videur disque ultérieur (une data
    générée mais jamais accédée depuis N jours peut être purgée).

Usage:
    from modules.sql.catalogue_genere import GenereCatalogue
    gen = GenereCatalogue()          # RAM + disque + flusher
    gen.upsert(project_id=0, id_data="petri:llm-code", kind="petri",
               value="...", inputs_hash="h1")
    row = gen.get(0, "petri:llm-code")
    backup_id = gen.log_backup(0, "petri:llm-code", reason="avant modif règles")
    gen.close()
"""

import json
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sql.schema import mw_home

SCHEMA = Path(__file__).resolve().parent / "catalogue_genere_schema.sql"

DEFAULT_CONFIG = {
    "flush_interval_s": "60",
    "ram_min_age_s": "300",
    "ram_max_lines": "5000",
    "runs_max": "10",
    "disk_max_age_s": "2592000",
}


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ts() -> float:
    return time.time()


class GenereCatalogue:
    """Repo du domaine data_genere avec hiérarchie RAM/disque + flusher.

    Deux connexions SQLite : une en mémoire (`:memory:`, la RAM) et une sur
    fichier (`catalogue_genere.db`, le dur). Le flusher (thread) pousse les
    écritures RAM → disque et évince la RAM.
    """

    def __init__(self, db_path: Optional[Path] = None,
                 auto_flush: bool = True):
        self.db_path = Path(db_path) if db_path else self._default_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

        # RAM : SQLite en mémoire (même schéma).
        self.ram = sqlite3.connect(":memory:", check_same_thread=False,
                                   isolation_level=None)
        self.ram.row_factory = sqlite3.Row
        self._ensure_schema(self.ram)

        # Disque : fichier persistant (même schéma).
        self.disk = sqlite3.connect(f"file:{self.db_path}?mode=rwc", uri=True,
                                    check_same_thread=False, isolation_level=None)
        self.disk.row_factory = sqlite3.Row
        self.disk.execute("PRAGMA journal_mode = WAL")
        self.disk.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema(self.disk)

        self.config = self._load_config()
        self._flush_thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        if auto_flush:
            self._start_flusher()

    # ── Setup ──────────────────────────────────────────────

    @staticmethod
    def _default_db() -> Path:
        return mw_home() / "catalogue_genere.db"

    def _ensure_schema(self, conn: sqlite3.Connection) -> None:
        if SCHEMA.exists():
            conn.executescript(SCHEMA.read_text())
        else:
            # Fallback minimal (schéma non trouvé) — les tables vides.
            conn.execute("""CREATE TABLE IF NOT EXISTS gen_data (
                project_id INTEGER NOT NULL, id_data TEXT NOT NULL,
                PRIMARY KEY (project_id, id_data))""")

    def _load_config(self) -> Dict[str, str]:
        cfg = dict(DEFAULT_CONFIG)
        try:
            rows = self.disk.execute(
                "SELECT key, value FROM gen_config WHERE project_id = 0"
            ).fetchall()
            for r in rows:
                cfg[r["key"]] = r["value"]
        except Exception:
            pass
        return cfg

    def cfg(self, key: str, default: str = "") -> str:
        return self.config.get(key, default)

    # ── Écritures (RAM d'abord ; le flusher pousse vers le disque) ──

    def upsert(self, project_id: int, id_data: str, *, name: str = "",
               kind: str = "symbol", path: str = "", ref_id: str = "",
               value: str = "", value_is_file: bool = False,
               dependencies_json: Optional[List[Dict[str, Any]]] = None,
               inputs_hash: str = "", status: str = "valid",
               generation_mode: str = "deterministic",
               questionned: Optional[int] = None,
               backup_of: str = "", backup_reason: str = "") -> Dict[str, Any]:
        """Crée ou met à jour une data_genere (RAM). Le flusher la poussera."""
        now = _now()
        with self._lock:
            self.ram.execute("""
                INSERT INTO gen_data (project_id, id_data, name, kind, path,
                    ref_id, value, value_is_file, dependencies_json,
                    inputs_hash, status, generation_mode, questionned,
                    backup_of, backup_reason, storage, last_access_at,
                    nb_access, generated_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ram',?,?,?,?)
                ON CONFLICT(project_id, id_data) DO UPDATE SET
                    name=excluded.name, kind=excluded.kind, path=excluded.path,
                    ref_id=excluded.ref_id, value=excluded.value,
                    value_is_file=excluded.value_is_file,
                    dependencies_json=excluded.dependencies_json,
                    inputs_hash=excluded.inputs_hash,
                    status=excluded.status,
                    generation_mode=excluded.generation_mode,
                    questionned=excluded.questionned,
                    storage='ram',
                    updated_at=excluded.updated_at
            """, (project_id, id_data, name, kind, path, ref_id, value,
                  int(value_is_file),
                  json.dumps(dependencies_json or []), inputs_hash, status,
                  generation_mode, questionned, backup_of, backup_reason,
                  now, 0, now, now))
        return self.get(project_id, id_data)

    def set_status(self, project_id: int, id_data: str, status: str) -> bool:
        with self._lock:
            cur = self.ram.execute(
                "UPDATE gen_data SET status = ?, updated_at = ? "
                "WHERE project_id = ? AND id_data = ?",
                (status, _now(), project_id, id_data))
            return cur.rowcount > 0

    def flag_question(self, project_id: int, id_data: str,
                      question: str) -> int:
        """Signale une data comme "étrange" (id_question). Retourne l'id."""
        with self._lock:
            cur = self.ram.execute(
                "INSERT INTO questions (question) VALUES (?)", (question,))
            qid = cur.lastrowid
            self.ram.execute(
                "UPDATE gen_data SET questionned = ?, updated_at = ? "
                "WHERE project_id = ? AND id_data = ?",
                (qid, _now(), project_id, id_data))
            self.disk.execute(
                "INSERT OR IGNORE INTO questions (id_question, question) "
                "VALUES (?, ?)", (qid, question))
            return qid

    def add_run(self, project_id: int, data_ref: str, *,
                value: str = "", value_is_file: bool = False,
                inputs_hash: str = "", error: str = "") -> int:
        """Ring buffer stochastic : réécrit le run le plus vieux au-delà de
        runs_max. Retourne le run_seq écrit."""
        max_runs = int(self.cfg("runs_max", "10")) or 10
        with self._lock:
            rows = self.ram.execute(
                "SELECT run_seq FROM gen_runs WHERE project_id = ? AND data_ref = ? "
                "ORDER BY run_seq", (project_id, data_ref)).fetchall()
            seqs = [r["run_seq"] for r in rows]
            if len(seqs) < max_runs:
                seq = max(seqs) + 1 if seqs else 1
            else:
                seq = seqs[0]  # réécrit le plus vieux
            self.ram.execute("""
                INSERT INTO gen_runs (project_id, data_ref, run_seq, value,
                    value_is_file, inputs_hash, error, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                ON CONFLICT(project_id, data_ref, run_seq) DO UPDATE SET
                    value=excluded.value, value_is_file=excluded.value_is_file,
                    inputs_hash=excluded.inputs_hash, error=excluded.error,
                    created_at=excluded.created_at
            """, (project_id, data_ref, seq, value, int(value_is_file),
                  inputs_hash, error, _now()))
            return seq

    def list_runs(self, project_id: int, data_ref: str) -> List[Dict[str, Any]]:
        rows = self.ram.execute(
            "SELECT * FROM gen_runs WHERE project_id = ? AND data_ref = ? "
            "ORDER BY run_seq", (project_id, data_ref)).fetchall()
        return [dict(r) for r in rows]

    # ── Lecture : RAM → disque → promotion RAM ──

    def get(self, project_id: int, id_data: str) -> Optional[Dict[str, Any]]:
        now = _now()
        with self._lock:
            row = self.ram.execute(
                "SELECT * FROM gen_data WHERE project_id = ? AND id_data = ?",
                (project_id, id_data)).fetchone()
            if row is None:
                row = self.disk.execute(
                    "SELECT * FROM gen_data WHERE project_id = ? AND id_data = ?",
                    (project_id, id_data)).fetchone()
                if row is None:
                    return None
                # promotion RAM
                self.ram.execute("""
                    INSERT INTO gen_data SELECT * FROM gen_data WHERE 0
                """)  # no-op ; réinsertion explicite ci-dessous
                d = dict(row)
                self.ram.execute("""
                    INSERT INTO gen_data (project_id, id_data, name, kind, path,
                        ref_id, value, value_is_file, dependencies_json,
                        inputs_hash, status, generation_mode, questionned,
                        backup_of, backup_reason, storage, last_access_at,
                        nb_access, generated_at, updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'ram',?,?,?,?)
                """, (d["project_id"], d["id_data"], d["name"], d["kind"],
                      d["path"], d["ref_id"], d["value"], d["value_is_file"],
                      d["dependencies_json"], d["inputs_hash"], d["status"],
                      d["generation_mode"], d["questionned"], d["backup_of"],
                      d["backup_reason"], now, d["nb_access"], d["generated_at"],
                      now))
                row = self.ram.execute(
                    "SELECT * FROM gen_data WHERE project_id = ? AND id_data = ?",
                    (project_id, id_data)).fetchone()
            # bump last_access + nb_access (RAM et disque)
            self.ram.execute(
                "UPDATE gen_data SET last_access_at = ?, nb_access = nb_access + 1 "
                "WHERE project_id = ? AND id_data = ?",
                (now, project_id, id_data))
            self.disk.execute(
                "UPDATE gen_data SET last_access_at = ?, nb_access = ? "
                "WHERE project_id = ? AND id_data = ?",
                (now, int(dict(row).get("nb_access", 0) or 0) + 1,
                 project_id, id_data))
            return dict(row)

    def list(self, project_id: int, kind: str = "",
             status: str = "") -> List[Dict[str, Any]]:
        q = "SELECT * FROM gen_data WHERE project_id = ?"
        args: List[Any] = [project_id]
        if kind:
            q += " AND kind = ?"
            args.append(kind)
        if status:
            q += " AND status = ?"
            args.append(status)
        q += " ORDER BY updated_at"
        with self._lock:
            ram_rows = [dict(r) for r in self.ram.execute(q, args).fetchall()]
            disk_rows = self.disk.execute(q, args).fetchall()
            seen = {r["id_data"] for r in ram_rows}
            for r in disk_rows:
                if r["id_data"] not in seen:
                    ram_rows.append(dict(r))
            return ram_rows

    def delete(self, project_id: int, id_data: str = "",
               backup_of: str = "") -> int:
        """Supprime (RAM + disque). id_data vide → par backup_of (purge backups)."""
        with self._lock:
            if id_data:
                self.ram.execute(
                    "DELETE FROM gen_data WHERE project_id = ? AND id_data = ?",
                    (project_id, id_data))
                return self.disk.execute(
                    "DELETE FROM gen_data WHERE project_id = ? AND id_data = ?",
                    (project_id, id_data)).rowcount
            if backup_of:
                self.ram.execute(
                    "DELETE FROM gen_data WHERE project_id = ? AND backup_of = ?",
                    (project_id, backup_of))
                return self.disk.execute(
                    "DELETE FROM gen_data WHERE project_id = ? AND backup_of = ?",
                    (project_id, backup_of)).rowcount
            return 0

    def purge_project(self, project_id: int) -> int:
        """Vide tout le domaine d'un projet (data + runs + dependances)."""
        n = 0
        with self._lock:
            for tbl in ("gen_data", "gen_runs", "gen_dependance"):
                cur = self.ram.execute(
                    f"DELETE FROM {tbl} WHERE project_id = ?", (project_id,))
                cur = self.disk.execute(
                    f"DELETE FROM {tbl} WHERE project_id = ?", (project_id,))
                n += cur.rowcount
        return n

    # ── Dépendances ─────────────────────────────────────────

    def add_dependency(self, project_id: int, data_ref: str, dep_ref: str,
                       dep_version: str = "", role: str = "implementation") -> None:
        with self._lock:
            self.ram.execute("""
                INSERT OR IGNORE INTO gen_dependance
                    (project_id, data_ref, dep_ref, dep_version, role)
                VALUES (?,?,?,?,?)
            """, (project_id, data_ref, dep_ref, dep_version, role))
            self.disk.execute("""
                INSERT OR IGNORE INTO gen_dependance
                    (project_id, data_ref, dep_ref, dep_version, role)
                VALUES (?,?,?,?,?)
            """, (project_id, data_ref, dep_ref, dep_version, role))

    def dependencies(self, project_id: int, data_ref: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.disk.execute(
                "SELECT * FROM gen_dependance WHERE project_id = ? AND data_ref = ? "
                "ORDER BY role, dep_ref", (project_id, data_ref)).fetchall()
            return [dict(r) for r in rows]

    def dependents(self, project_id: int, dep_ref: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.disk.execute(
                "SELECT * FROM gen_dependance WHERE project_id = ? AND dep_ref = ? "
                "ORDER BY data_ref", (project_id, dep_ref)).fetchall()
            return [dict(r) for r in rows]

    # ── Fichiers runtime (glob) : vérification inverse au get ──

    def register_runtime_glob(self, project_id: int, data_ref: str,
                              glob: str) -> None:
        """Déclare que `data_ref` lit les fichiers du glob runtime (ex.
        "logs/*.log"). Vérifié au get de la data (pas par le watcher)."""
        with self._lock:
            self.disk.execute("""
                INSERT OR IGNORE INTO gen_runtime_files
                    (project_id, data_ref, glob, last_read_mtime)
                VALUES (?, ?, ?, 0)
            """, (project_id, data_ref, glob))

    def runtime_globs(self, project_id: int,
                      data_ref: str) -> List[Dict[str, Any]]:
        with self._lock:
            rows = self.disk.execute(
                "SELECT * FROM gen_runtime_files "
                "WHERE project_id = ? AND data_ref = ?",
                (project_id, data_ref)).fetchall()
            return [dict(r) for r in rows]

    def check_runtime(self, project_id: int, data_ref: str,
                      base: Optional["Path"] = None) -> bool:
        """Vérification INVERSE au get : si un fichier du glob runtime a un
        mtime plus récent que last_read_mtime, la data est stale (le contenu
        analysé a pu changer). Met à jour last_read_mtime. Retourne True si
        stale (à régénérer).

        `base` : racine du projet pour résoudre les globs (défaut mw_home)."""
        from pathlib import Path
        if base is None:
            from services._common import mw_home
            base = mw_home()
        stale = False
        for row in self.runtime_globs(project_id, data_ref):
            glob = row["glob"]
            matches = list(Path(base).glob(glob)) if glob else []
            max_mtime = 0.0
            for m in matches:
                try:
                    mt = m.stat().st_mtime_ns / 1e9
                    if mt > max_mtime:
                        max_mtime = mt
                except OSError:
                    continue
            if max_mtime > float(row["last_read_mtime"] or 0) \
                    and float(row["last_read_mtime"] or 0) > 0:
                stale = True
            with self._lock:
                self.disk.execute(
                    "UPDATE gen_runtime_files SET last_read_mtime = ? "
                    "WHERE project_id = ? AND data_ref = ? AND glob = ?",
                    (max_mtime, project_id, data_ref, glob))
        try:
            self.disk.commit()
        except Exception:
            pass
        return stale

    # ── Backup log (snapshot figé) ──────────────────────────

    def log_backup(self, project_id: int, id_data: str,
                   reason: str = "") -> Optional[str]:
        """Snapshote une data_genere validée (hors invalidation).

        Copie value + dépendances + inputs_hash dans une nouvelle ligne
        (id = "<id_data>@snap_<ts>", backup_of = id_data). Le snapshot reste
        figé même si l'original est re-généré/stale — permet de diff
        "avant" vs "après" un changement."""
        src = self.get(project_id, id_data)
        if src is None:
            return None
        bid = f"{id_data}@snap_{int(_ts())}"
        self.upsert(project_id, bid, name=src.get("name", ""),
                    kind=src.get("kind", ""), path=src.get("path", ""),
                    ref_id=src.get("ref_id", ""),
                    value=src.get("value", ""),
                    value_is_file=bool(src.get("value_is_file")),
                    dependencies_json=_safe_json(src.get("dependencies_json")),
                    inputs_hash=src.get("inputs_hash", ""),
                    status="valid", generation_mode=src.get("generation_mode",
                                                            "deterministic"),
                    backup_of=id_data, backup_reason=reason)
        # Copie les dépendances du snapshot
        for d in self.dependencies(project_id, id_data):
            self.add_dependency(project_id, bid, d["dep_ref"],
                                d["dep_version"], d["role"])
        return bid

    def diff_data(self, project_id: int, backup_id: str,
                  current_id: str) -> Optional[Dict[str, Any]]:
        """Compare un snapshot (backup_id) à la data courante."""
        a = self.get(project_id, backup_id)
        b = self.get(project_id, current_id)
        if a is None or b is None:
            return None
        return {
            "same_hash": a.get("inputs_hash") == b.get("inputs_hash"),
            "same_value": a.get("value") == b.get("value"),
            "inputs_hash_backup": a.get("inputs_hash"),
            "inputs_hash_current": b.get("inputs_hash"),
            "backup_of": a.get("backup_of", ""),
        }

    # ── Flusher : RAM → disque + éviction ──────────────────

    def _start_flusher(self) -> None:
        self._stop.clear()
        self._flush_thread = threading.Thread(
            target=self._flush_loop, name="genere_flusher", daemon=True)
        self._flush_thread.start()

    def _flush_loop(self) -> None:
        while not self._stop.wait(float(self.cfg("flush_interval_s", "60")) or 60):
            try:
                self.flush()
            except Exception:
                pass

    def flush(self) -> int:
        """Pousse toutes les lignes RAM vers le disque (INSERT ON CONFLICT),
        puis évince la RAM au-delà de ram_max_lines (hors min_age récent)."""
        cols = ("project_id", "id_data", "name", "kind", "path", "ref_id",
                "value", "value_is_file", "dependencies_json", "inputs_hash",
                "status", "generation_mode", "questionned", "backup_of",
                "backup_reason", "storage", "last_access_at", "nb_access",
                "generated_at", "updated_at")
        ph = ",".join("?" * len(cols))
        up = ",".join(f"{c}=excluded.{c}" for c in cols)
        with self._lock:
            rows = self.ram.execute("SELECT * FROM gen_data").fetchall()
            for r in rows:
                self.disk.execute(
                    f"INSERT INTO gen_data ({','.join(cols)}) VALUES ({ph}) "
                    f"ON CONFLICT(project_id, id_data) DO UPDATE SET {up}",
                    tuple(r[c] for c in cols))
            n = len(rows)
            self._evict_ram()
        return n

    def _evict_ram(self) -> None:
        """Supprime les lignes RAM les plus vieilles (déjà sur disque) si la
        RAM dépasse ram_max_lines, sans retirer les data accédées il y a moins
        de ram_min_age_s."""
        max_lines = int(self.cfg("ram_max_lines", "5000")) or 5000
        min_age = float(self.cfg("ram_min_age_s", "300")) or 300
        count = self.ram.execute("SELECT COUNT(*) AS n FROM gen_data").fetchone()["n"]
        if count <= max_lines:
            return
        cutoff = _now_shift(-min_age)
        # à évincer : vieilles (> min_age), déjà sur disque (= tout en RAM)
        self.ram.execute(
            "DELETE FROM gen_data WHERE storage = 'ram' "
            "AND COALESCE(last_access_at, '') < ? "
            "AND id_data IN (SELECT id_data FROM gen_data ORDER BY last_access_at "
            "LIMIT ?)",
            (cutoff, max(0, count - max_lines)))


def _safe_json(raw: Any) -> List[Dict[str, Any]]:
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return []
    return []


def _now_shift(seconds: float) -> str:
    from datetime import timedelta
    dt = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=seconds)
    return dt.isoformat()


__all__ = ["GenereCatalogue", "DEFAULT_CONFIG", "SCHEMA"]
