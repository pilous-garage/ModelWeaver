from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.genere import read, write


def _now() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()


def _ts() -> float:
    return time.time()


def now_epoch() -> float:
    return time.time()


def data_id_of(path_data: str) -> int:
    """id_data stable (uint) d'un path_data texte.

    xxhash64 du texte → uint64. En cas de collision théorique, on incarne une
    clé alternée. path_data est immutable (chemin logique de la data), donc le
    hash est stable de vie. Retourne un int (SQLite INTEGER)."""
    if not path_data:
        raise ValueError("path_data vide")
    try:
        import xxhash
        h = xxhash.xxh64(path_data, seed=0xC7A3_1F2B).intdigest()
    except ImportError:
        import hashlib
        h = int.from_bytes(hashlib.sha256(path_data.encode("utf-8")).digest()[:8], "big")
    if h == 0:
        h = 1
    return h


class DataGenere:
    """Objet-entité lié à UNE data_genere (project_id, path_data).

    Équivalent de DataTable (local) mais sur la table unique `gen_data`
    (toutes les `kind` partagent le schéma). Centralise le refresh (304),
    les runs stochastiques, les dépendances, le backup/diff et la
    staleness fichier. La persistance brute (upsert/DELETE) reste thin
    dans read/write.py.

    V3 : id_data = INTEGER (uint hash(path_data)) ; path_data = texte lisible
    ("kind:scope:name"). Les data_ref (FK) sur gen_runs/gen_dependance/
    gen_runtime_files sont des id_data (int)."""

    def __init__(self, db: Db, project_id: int, path_data: str):
        self.db = db
        self.project_id = project_id
        self.path_data = path_data
        self.id_data = data_id_of(path_data)

    # ── existence / get ──
    def exists(self) -> bool:
        return read.get_data(self.db, self.project_id, self.path_data) is not None

    def get(self) -> Dict[str, Any]:
        write.bump_access(self.db, self.project_id, self.path_data)
        e = read.get_data(self.db, self.project_id, self.path_data)
        if not e:
            raise KeyError(f"gen_data introuvable: {self.project_id}/{self.path_data}")
        try:
            e["dependencies"] = json.loads(e["dependencies_json"] or "[]")
        except Exception:
            e["dependencies"] = []
        e["id_data"] = self.id_data
        return e

    # ── mutation (thin → write) ──
    def set_value(self, *, name: str = "", kind: str = "symbol", value: str = "",
                  value_is_file: bool = False, dependencies_json: str = "[]",
                  inputs_hash: str = "", status: str = "valid") -> Dict[str, Any]:
        return write.upsert_data(self.db, self.project_id, self.path_data,
                                 name=name, kind=kind, value=value,
                                 value_is_file=value_is_file,
                                 dependencies_json=dependencies_json,
                                 inputs_hash=inputs_hash, status=status)

    def set_status(self, status: str) -> int:
        return write.set_status(self.db, self.project_id, self.path_data, status)

    def set_inputs_hash(self, inputs_hash: str) -> int:
        return write.set_inputs_hash(self.db, self.project_id, self.path_data,
                                     inputs_hash)

    # ── runs stochastiques (ring-buffer) ──
    def add_run(self, *, value: str = "", value_is_file: bool = False,
                inputs_hash: str = "", error: str = "") -> int:
        """Ring buffer : réécrit le run le plus vieux au-delà de runs_max."""
        max_runs = int(self._cfg("runs_max", "10")) or 10
        rows = read.list_runs(self.db, self.project_id, self.id_data)
        seqs = [r["run_seq"] for r in rows]
        if not seqs:
            seq = 1
        elif len(seqs) < max_runs:
            seq = max(seqs) + 1
        else:
            seq = seqs[0]
        self.db.table("gen_runs").upsert(
            {"project_id": self.project_id, "data_ref": self.id_data,
             "run_seq": seq, "value": value, "value_is_file": int(value_is_file),
             "inputs_hash": inputs_hash, "error": error,
             "created_at": _now()},
            ["project_id", "data_ref", "run_seq"])
        return seq

    def list_runs(self) -> List[Dict[str, Any]]:
        return read.list_runs(self.db, self.project_id, self.id_data)

    # ── dépendances ──
    def add_dependency(self, dep_ref: str, dep_version: str = "",
                       role: str = "implementation") -> None:
        write.add_dependency(self.db, self.project_id, self.id_data, dep_ref,
                             dep_version, role)

    def dependencies(self) -> List[Dict[str, Any]]:
        return read.get_dependencies(self.db, self.project_id, self.id_data)

    def dependents(self) -> List[Dict[str, Any]]:
        return read.get_dependents(self.db, self.project_id, self.path_data)

    # ── staleness fichier (vérif inverse au get) ──
    def is_runtime_stale(self, base: str = "") -> bool:
        b = Path(base) if base else Path(".")
        stale = False
        for row in read.runtime_globs(self.db, self.project_id, self.id_data):
            glob = row.get("glob")
            if not glob:
                continue
            matches = list(b.glob(glob))
            max_mtime = 0.0
            for m in matches:
                try:
                    mt = m.stat().st_mtime_ns / 1e9
                    if mt > max_mtime:
                        max_mtime = mt
                except OSError:
                    continue
            last = float(row.get("last_read_mtime") or 0)
            if last > 0 and max_mtime > last:
                stale = True
            if max_mtime > 0:
                self.db.table("gen_runtime_files").update(
                    {"project_id": self.project_id, "data_ref": self.id_data,
                     "glob": glob}, {"last_read_mtime": max_mtime})
        return stale

    def register_runtime_glob(self, glob: str) -> None:
        write.register_runtime_glob(self.db, self.project_id, self.id_data, glob)

    def record_file(self, ref_file: str, last_modif_ts: float = 0,
                    last_hash: str = "") -> None:
        write.upsert_file_stat(self.db, self.project_id, ref_file,
                               last_modif_ts=last_modif_ts, last_hash=last_hash)

    def is_file_stale(self, ref_file: str) -> bool:
        """Stale si le fichier a changé depuis le dernier hash (mtime > hash_ts)."""
        row = read.get_file_stat(self.db, self.project_id, ref_file)
        if not row:
            return True
        last_hash_ts = row.get("last_hash_ts") or 0
        last_modif_ts = row.get("last_modif_ts") or 0
        return last_hash_ts <= 0 or last_modif_ts > last_hash_ts

    # ── refresh / backup / diff (logique) ──
    def refresh(self, last_access: str = "") -> Dict[str, Any]:
        """304 : data modifiée si updated_at > last_access ; aucune écriture."""
        row = self.db.table("gen_data").get(
            {"project_id": self.project_id, "id_data": self.id_data},
            cols=["project_id", "id_data", "path_data", "name", "updated_at"])
        if not row:
            raise KeyError(f"data introuvable: {self.project_id}/{self.path_data}")
        if last_access and row.get("updated_at") \
                and str(row["updated_at"]) <= str(last_access):
            return {"modified": False, "id_data": self.id_data,
                    "path_data": self.path_data, "ref": row.get("name", "")}
        return {"modified": True, "id_data": self.id_data, "path_data": self.path_data,
                "updated_at": row.get("updated_at")}

    def log_backup(self, reason: str = "") -> Optional[int]:
        """Snapshot de la data courante sous un nouveau path_data
        '<self.path_data>@snap_<epoch>'. Retourne le nouvel id_data (int)."""
        src = self.get()
        if not src:
            return None
        backup_epoch = int(_ts())
        backup_path = f"{self.path_data}@snap_{backup_epoch}"
        bid = data_id_of(backup_path)
        now = _ts()
        self.db.table("gen_data").upsert(
            {"project_id": self.project_id, "id_data": bid,
             "path_data": backup_path,
             "name": src.get("name", ""), "kind": src.get("kind", "symbol"),
             "value": src.get("value", ""), "value_is_file": src.get("value_is_file", 0),
             "dependencies_json": src.get("dependencies_json", "[]"),
             "inputs_hash": src.get("inputs_hash", ""), "status": "valid",
             "storage": "disk", "last_access_at": now, "nb_access": 0,
             "generated_at": now, "updated_at": now, "last_modify": now,
             "backup_of_id": self.id_data, "backup_reason": reason},
            ["project_id", "id_data"])
        for d in src.get("dependencies", []):
            self.db.table("gen_dependance").upsert(
                {"project_id": self.project_id, "data_ref": bid,
                 "dep_ref": d.get("dep_ref", ""), "dep_version": d.get("dep_version", ""),
                 "role": d.get("role", "implementation")},
                ["project_id", "data_ref", "dep_ref", "dep_version"])
        return bid

    def diff(self, backup_id: int) -> Optional[Dict[str, Any]]:
        a = read.get_data_by_id(self.db, self.project_id, backup_id)
        b = read.get_data(self.db, self.project_id, self.path_data)
        if a is None or b is None:
            return None
        return {"same_hash": a.get("inputs_hash") == b.get("inputs_hash"),
                "same_value": a.get("value") == b.get("value"),
                "inputs_hash_backup": a.get("inputs_hash"),
                "inputs_hash_current": b.get("inputs_hash")}

    def delete(self) -> int:
        return write.delete_data(self.db, self.project_id, self.path_data)

    def delete_backups(self) -> int:
        return write.delete_data(self.db, self.project_id, backup_of_id=self.id_data)

    # ── helpers ──
    def _cfg(self, key: str, default: str = "") -> str:
        row = read.get_config(self.db, self.project_id, key)
        return row["value"] if row else default


def data_genere(db: Db, project_id: int, path_data: str, *,
                kind: str = "symbol", name: str = "", value: str = "{}",
                value_is_file: bool = False) -> DataGenere:
    """Factory : crée (stub) + retourne l'objet DataGenere lié à l'entité.

    V3 : prend `path_data` (texte lisible) ; id_data = hash(path_data)."""
    if not read.get_data(db, project_id, path_data):
        write.upsert_data(db, project_id, path_data, name=name or path_data,
                          kind=kind, value=value, value_is_file=value_is_file)
    return DataGenere(db, project_id, path_data)
