"""StreamBus — Bus de diffusion des chunks d'exécution des agents (Phase 4).

Deux modes :
  - In-process (StreamBus) : singleton mémoire, rapide, threadsafe.
  - Cross-process (StreamBusDB) : SQLite WAL, partagé entre gateway et AFD.
    Le singleton cross-process s'active automatiquement si le fichier DB existe.

Usage in-process :
    from AgentFrameWork.stream_bus import stream_bus
    seq = stream_bus.publish(agent_id, "Hello", "token")
    chunks = stream_bus.since(agent_id, 0)

Usage cross-process :
    bus = StreamBusDB("/tmp/mw_stream.db")
    bus.publish(agent_id, "Hello")
    # Le gateway lit depuis le même fichier via StreamBusDB
"""

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from services._common import mw_home


class StreamBus:
    """Buffer circulaire de chunks par agent_id (mémoire uniquement)."""

    def __init__(self, max_per_agent: int = 2000):
        self._lock = threading.Lock()
        self._buffers: Dict[int, List[Dict[str, Any]]] = {}
        self._seq = 0
        self._max = max_per_agent
        self._paused: Dict[int, bool] = {}

    def set_paused(self, agent_id: int, paused: bool) -> None:
        with self._lock:
            self._paused[agent_id] = paused
            if not paused:
                # purge les chunks en attente ? on garde le buffer tel quel
                pass

    def is_paused(self, agent_id: int) -> bool:
        with self._lock:
            return self._paused.get(agent_id, False)

    def publish(self, agent_id: int, chunk: str, kind: str = "token") -> int:
        with self._lock:
            if self._paused.get(agent_id, False):
                # pendant la pause, on ignore les chunks (stream suspendu)
                return -1
            self._seq += 1
            seq = self._seq
            buf = self._buffers.setdefault(agent_id, [])
            buf.append({
                "seq": seq,
                "ts": time.time(),
                "chunk": chunk,
                "kind": kind,
            })
            if len(buf) > self._max:
                del buf[0:len(buf) - self._max]
            return seq

    def since(self, agent_id: int, seq: int) -> List[Dict[str, Any]]:
        with self._lock:
            return [e for e in self._buffers.get(agent_id, []) if e["seq"] > seq]

    def reset(self, agent_id: int) -> None:
        with self._lock:
            self._buffers.pop(agent_id, None)
            self._paused.pop(agent_id, None)


class StreamBusDB:
    """StreamBus cross-process via SQLite WAL.

    Un seul writer par agent_id = zero contention d'écriture.
    Le gateway (lecteur) poll via `since()` — pas de lock bloquant.
    """

    def __init__(self, db_path: Optional[str] = None):
        path = Path(db_path) if db_path else (mw_home() / "stream_bus.db")
        parent = path.parent
        parent.mkdir(parents=True, exist_ok=True)
        self._path = str(path)
        self._conn: Optional[sqlite3.Connection] = None
        self._write_lock = threading.Lock()
        self._seq_lock = threading.Lock()
        self._seq = 0
        self._init_db()

    def _init_db(self):
        url = f"file:{self._path}?mode=rwc"
        self._conn = sqlite3.connect(url, uri=True, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS stream_events (
                agent_id INTEGER NOT NULL,
                seq      INTEGER NOT NULL,
                ts       REAL NOT NULL,
                chunk    TEXT NOT NULL,
                kind     TEXT NOT NULL DEFAULT 'token'
            )
        """)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS ix_stream_agent_seq ON stream_events (agent_id, seq)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS stream_pause_state (
                agent_id INTEGER NOT NULL PRIMARY KEY,
                paused   INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            )
        """)
        self._conn.commit()

    def set_paused(self, agent_id: int, paused: bool) -> None:
        with self._write_lock:
            self._conn.execute(
                "INSERT OR REPLACE INTO stream_pause_state (agent_id, paused, updated_at) VALUES (?, ?, ?)",
                (agent_id, 1 if paused else 0, time.time()),
            )
            self._conn.commit()

    def is_paused(self, agent_id: int) -> bool:
        row = self._conn.execute(
            "SELECT paused FROM stream_pause_state WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
        return bool(row["paused"]) if row else False

    def publish(self, agent_id: int, chunk: str, kind: str = "token") -> int:
        with self._write_lock:
            if self.is_paused(agent_id):
                return -1
            with self._seq_lock:
                self._seq += 1
                seq = self._seq
            self._conn.execute(
                "INSERT INTO stream_events (agent_id, seq, ts, chunk, kind) "
                "VALUES (?, ?, ?, ?, ?)",
                (agent_id, seq, time.time(), chunk, kind))
            self._conn.commit()
        return seq

    def since(self, agent_id: int, seq: int) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM stream_events WHERE agent_id = ? AND seq > ? ORDER BY seq",
            (agent_id, seq)).fetchall()
        return [dict(r) for r in rows]

    def reset(self, agent_id: int) -> None:
        with self._write_lock:
            self._conn.execute(
                "DELETE FROM stream_events WHERE agent_id = ?", (agent_id,))
            self._conn.commit()

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None


# Singletons

# in-process (backward-compatible, legacy code)
_inproc_bus = StreamBus()
_crossproc_bus: Optional[StreamBusDB] = None
_crossproc_lock = threading.Lock()


def _active() -> StreamBusDB | StreamBus:
    """Retourne le bus actif (cross-process si activé, sinon in-process)."""
    with _crossproc_lock:
        if _crossproc_bus is not None:
            return _crossproc_bus
    return _inproc_bus


class _StreamBusFacade:
    """Singleton facade qui délègue au bus actif (mémoire ou SQLite WAL)."""

    def publish(self, agent_id: int, chunk: str, kind: str = "token") -> int:
        return _active().publish(agent_id, chunk, kind)

    def since(self, agent_id: int, seq: int) -> list:
        return _active().since(agent_id, seq)

    def reset(self, agent_id: int) -> None:
        _active().reset(agent_id)

    def set_paused(self, agent_id: int, paused: bool) -> None:
        _active().set_paused(agent_id, paused)

    def is_paused(self, agent_id: int) -> bool:
        return _active().is_paused(agent_id)


# Singleton unique — dispatch automatique entre mémoire et cross-process.
stream_bus = _StreamBusFacade()


def activate_cross_process(db_path: Optional[str] = None) -> StreamBusDB:
    """Active le bus cross-process SQLite WAL pour le streaming.

    Doit être appelé au démarrage de l'AFD (écrivain) et du gateway (lecteur).
    Tous partagent le même fichier SQLite WAL.
    """
    global _crossproc_bus
    path = db_path or resolve_stream_path()
    with _crossproc_lock:
        if _crossproc_bus is not None:
            _crossproc_bus.close()
        _crossproc_bus = StreamBusDB(path)
    return _crossproc_bus


def get_stream_bus(db_path: Optional[str] = None) -> StreamBusDB:
    return StreamBusDB(db_path)


def resolve_stream_path() -> str:
    shm = Path("/dev/shm")
    if shm.exists() and shm.is_dir():
        return str(shm / "mw_stream_bus.db")
    return str(mw_home() / "stream_bus.db")