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


class DataGenere:
    """Objet-entité lié à UNE data_genere (project_id, id_data).

    Équivalent de DataTable (local) mais sur la table unique `gen_data`
    (toutes les `kind` partagent le schéma). Centralise le refresh (304),
    les runs stochastiques, les dépendances, le backup/diff et la
    staleness fichier. La persistance brute (upsert/DELETE) reste thin
    dans read/write.py."""

    def __init__(self, db: Db, project_id: int, id_data: str):
        self.db = db
        self.project_id = project_id
        self.id_data = id_data

    # ── existence / get ───────────────────────────────────────
    def exists(self) -> bool:
        return read.get_data(self.db, self.project_id, self.id_data) is not None

    def get(self) -> Dict[str, Any]:
        write.bump_access(self.db, self.project_id, self.id_data)
        e = read.get_data(self.db, self.project_id, self.id_data)
        if not e:
            raise KeyError(f"gen_data introuvable: {self.project_id}/{self.id_data}")
        try:
            e["dependencies"] = json.loads(e["dependencies_json"] or "[]")
        except Exception:
            e["dependencies"] = []
        return e

    # ── mutation (thin → write) ───────────────────────────────
    def set_value(self, *, name: str = "", kind: str = "symbol", value: str = "",
                  value_is_file: bool = False, dependencies_json: str = "[]",
                  inputs_hash: str = "", status: str = "valid") -> Dict[str, Any]:
        return write.upsert_data(self.db, self.project_id, self.id_data,
                                 name=name, kind=kind, value=value,
                                 value_is_file=value_is_file,
                                 dependencies_json=dependencies_json,
                                 inputs_hash=inputs_hash, status=status)

    def set_status(self, status: str) -> int:
        return write.set_status(self.db, self.project_id, self.id_data, status)

    def set_inputs_hash(self, inputs_hash: str) -> int:
        return write.set_inputs_hash(self.db, self.project_id, self.id_data,
                                     inputs_hash)

    # ── runs stochastiques (ring-buffer) ─────────────────────
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
            seq = seqs[0]  # réécrit le plus vieux
        self.db.table("gen_runs").upsert(
            {"project_id": self.project_id, "data_ref": self.id_data,
             "run_seq": seq, "value": value, "value_is_file": int(value_is_file),
             "inputs_hash": inputs_hash, "error": error,
             "created_at": _now()},
            ["project_id", "data_ref", "run_seq"])
        return seq

    def list_runs(self) -> List[Dict[str, Any]]:
        return read.list_runs(self.db, self.project_id, self.id_data)

    # ── dépendances ──────────────────────────────────────────
    def add_dependency(self, dep_ref: str, dep_version: str = "",
                       role: str = "implementation") -> None:
        write.add_dependency(self.db, self.project_id, self.id_data, dep_ref,
                             dep_version, role)

    def dependencies(self) -> List[Dict[str, Any]]:
        return read.get_dependencies(self.db, self.project_id, self.id_data)

    def dependents(self) -> List[Dict[str, Any]]:
        return read.get_dependents(self.db, self.project_id, self.id_data)

    # ── staleness fichier (vérif inverse au get) ─────────────
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
            # rafraîchit toujours la trace (contrairement au bug V2, on
            # met à jour même sans staleness — nécessaire pour détecter le
            # prochain changement).
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

    # ── refresh / backup / diff (logique) ────────────────────
    def refresh(self, last_access: str = "") -> Dict[str, Any]:
        """304 : data modifiée si updated_at > last_access ; aucune écriture."""
        row = self.db.table("gen_data").get(
            {"project_id": self.project_id, "id_data": self.id_data},
            cols=["project_id", "id_data", "name", "updated_at"])
        if not row:
            raise KeyError(f"data introuvable: {self.project_id}/{self.id_data}")
        if last_access and row.get("updated_at") \
                and str(row["updated_at"]) <= str(last_access):
            return {"modified": False, "id_data": self.id_data, "ref": row.get("name", "")}
        return {"modified": True, "id_data": self.id_data,
                "updated_at": row.get("updated_at")}

    def log_backup(self, reason: str = "") -> Optional[str]:
        src = self.get()
        if not src:
            return None
        bid = f"{self.id_data}@snap_{int(_ts())}"
        self.db.table("gen_data").upsert(
            {"project_id": self.project_id, "id_data": bid,
             "name": src.get("name", ""), "kind": src.get("kind", "symbol"),
             "path": src.get("path", ""), "ref_id": src.get("ref_id", ""),
             "value": src.get("value", ""), "value_is_file": src.get("value_is_file", 0),
             "dependencies_json": src.get("dependencies_json", "[]"),
             "inputs_hash": src.get("inputs_hash", ""), "status": "valid",
             "backup_of": self.id_data, "backup_reason": reason,
             "storage": "disk", "last_access_at": _now(), "nb_access": 0,
             "generated_at": _now(), "updated_at": _now()},
            ["project_id", "id_data"])
        # reprend les dépendances du snapshot
        for d in src.get("dependencies", []):
            self.db.table("gen_dependance").upsert(
                {"project_id": self.project_id, "data_ref": bid,
                 "dep_ref": d.get("dep_ref", ""), "dep_version": d.get("dep_version", ""),
                 "role": d.get("role", "implementation")},
                ["project_id", "data_ref", "dep_ref", "dep_version"])
        return bid

    def diff(self, backup_id: str) -> Optional[Dict[str, Any]]:
        a = read.get_data(self.db, self.project_id, backup_id)
        b = read.get_data(self.db, self.project_id, self.id_data)
        if a is None or b is None:
            return None
        return {"same_hash": a.get("inputs_hash") == b.get("inputs_hash"),
                "same_value": a.get("value") == b.get("value"),
                "inputs_hash_backup": a.get("inputs_hash"),
                "inputs_hash_current": b.get("inputs_hash")}

    def delete(self) -> int:
        return write.delete_data(self.db, self.project_id, self.id_data)

    # ── helpers ──────────────────────────────────────────────
    def _cfg(self, key: str, default: str = "") -> str:
        row = read.get_config(self.db, self.project_id, key)
        return row["value"] if row else default


def data_genere(db: Db, project_id: int, id_data: str, *,
                kind: str = "symbol", name: str = "", value: str = "{}",
                value_is_file: bool = False) -> DataGenere:
    """Factory : crée (stub) + retourne l'objet DataGenere lié à l'entité."""
    if not read.get_data(db, project_id, id_data):
        write.upsert_data(db, project_id, id_data, name=name or id_data,
                          kind=kind, value=value, value_is_file=value_is_file)
    return DataGenere(db, project_id, id_data)
