"""service_ticker — ticker maître des SERVICES à tick.

UN SEUL thread par service (singleton). Deux modes selon le rythme :

  - > 1s (éphémère) : thread jetable à chaque échéance. Le ticker crée une
    ligne dans `service_tick_runs` (thread_id = threading.ident) et VÉRIFIE
    réellement que le thread tourne (is_alive) — pas juste un flag. Si un
    thread éphémère dépasse `timeout_ticks` (10) échéances sans finir, il
    est marqué `timedout` (détaché) et un neuf est relancé.

  - 1s (permanent) : un singleton qui BOUCLE (`while: fn(); sleep(1)`) tant
    que le nombre de services 1s reste sous SEUIL_SECONDE_PERMANENT ; au-delà
    → bascule en éphémère. Le ticker ne relance le permanent QUE s'il est mort.

État des services en CACHE LOCAL (dict) + dupliqué en BDD (service_ticks /
service_ticks_secondes) — le ticker ne relit pas la BDD à chaque passage.
Le cache est trié par `next_tick` (min-heap) : les services à tick lent ne
sont visités que quand leur échéance approche.

Usage:
    from services.service_ticker import ServiceTicker
    st = ServiceTicker()
    st.register("file_watcher", interval_s=60, fn=fw.check_once)
    st.register("usage_tick", interval_s=1, fn=usage_tick)
    st.start()            # thread maître (daemon)

    # CLI : python3 -m services.service_ticker
"""

from __future__ import annotations

import heapq
import importlib
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from modules.sql.schema import mw_home

DEFAULT_TICK = 1.0        # cadence du ticker maître (s)
DEFAULT_MAX_RUN = 300.0   # durée max d'un service éphémère (s)
TIMEOUT_TICKS = 10        # un thread éphémère qui dépasse N échéances → relance
SEUIL_SECONDE_PERMANENT = 50  # au-delà → les services 1s passent en éphémère

# Callables résolus en mémoire : svc_name → fn.
_handlers: Dict[str, Callable[[], Any]] = {}


def _default_db() -> Path:
    return mw_home() / "modelweaver.db"


class ServiceTicker:
    """Ticker maître : singleton par service, threads éphémères vérifiés."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        import sqlite3
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        # CACHE LOCAL (source de vérité runtime) + duplicate BDD.
        self._svcs: Dict[str, dict] = {}   # name → {mode, interval, next_tick,
                                           #         starts, thread, run_id, fn}
        self._heap: List[tuple] = []       # (next_tick, name) — tri d'échéance
        self._lock = threading.Lock()
        self._stop = threading.Event()

    def _ensure_schema(self) -> None:
        try:
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS service_ticks ("
                "  tick_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  svc_name TEXT UNIQUE NOT NULL,"
                "  tick_interval_s REAL NOT NULL DEFAULT 60,"
                "  cmd TEXT DEFAULT '', last_launch REAL,"
                "  last_duration_s REAL, running INTEGER DEFAULT 0,"
                "  enabled INTEGER DEFAULT 1,"
                "  created_at TEXT DEFAULT (datetime('now')))")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS service_ticks_secondes ("
                "  tick_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  svc_name TEXT UNIQUE NOT NULL, cmd TEXT DEFAULT '',"
                "  last_launch REAL, last_duration_s REAL,"
                "  running INTEGER DEFAULT 0, enabled INTEGER DEFAULT 1,"
                "  created_at TEXT DEFAULT (datetime('now')))")
            self.conn.execute(
                "CREATE TABLE IF NOT EXISTS service_tick_runs ("
                "  run_id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "  svc_name TEXT NOT NULL, thread_id INTEGER,"
                "  started_at REAL, finished_at REAL, duration_s REAL,"
                "  status TEXT DEFAULT 'running',"
                "  created_at TEXT DEFAULT (datetime('now')))")
        except Exception:
            pass

    # ── Table BDD selon le mode ─────────────────────────────

    def _table_for(self, mode: str) -> str:
        return "service_ticks_secondes" if mode == "permanent" \
            else "service_ticks"

    def _flush(self, name: str, **fields) -> None:
        """Persiste l'état d'un service en BDD (le cache local est la source
        chaude ; on ne fait que dupliquer les champs de supervision)."""
        svc = self._svcs.get(name)
        if svc is None:
            return
        table = self._table_for(svc["mode"])
        cols, vals = [], []
        for k, v in fields.items():
            cols.append(f"{k} = ?")
            vals.append(v)
        if not cols:
            return
        vals.append(name)
        try:
            self.conn.execute(
                f"UPDATE {table} SET {', '.join(cols)} WHERE svc_name = ?",
                vals)
            self.conn.commit()
        except Exception:
            pass

    # ── Enregistrement ──────────────────────────────────────

    def register(self, svc_name: str, interval_s: float = 60,
                 fn: Optional[Callable[[], Any]] = None,
                 cmd: str = "") -> None:
        """Enregistre un service (singleton, cache local + BDD)."""
        mode = self._mode_for(interval_s)
        if fn is not None:
            _handlers[svc_name] = fn
        with self._lock:
            self._svcs[svc_name] = {
                "mode": mode, "interval": float(interval_s),
                "next_tick": 0.0, "starts": 0, "thread": None,
                "run_id": None, "fn": fn,
            }
            heapq.heappush(self._heap, (0.0, svc_name))
        table = self._table_for(mode)
        try:
            if mode == "permanent":
                # table secondes : pas de colonne tick_interval_s (toujours 1s)
                self.conn.execute(
                    f"INSERT INTO {table} (svc_name, cmd, enabled) "
                    f"VALUES (?, ?, 1) "
                    f"ON CONFLICT(svc_name) DO UPDATE SET "
                    f"cmd = excluded.cmd, enabled = 1",
                    (svc_name, cmd))
            else:
                self.conn.execute(
                    f"INSERT INTO {table} (svc_name, tick_interval_s, cmd, enabled) "
                    f"VALUES (?, ?, ?, 1) "
                    f"ON CONFLICT(svc_name) DO UPDATE SET "
                    f"tick_interval_s = excluded.tick_interval_s, "
                    f"cmd = excluded.cmd, enabled = 1",
                    (svc_name, float(interval_s), cmd))
            self.conn.commit()
        except Exception as e:
            print(f"[service_ticker] register {svc_name} BDD error: {e}")
        # permanent : démarre immédiatement le thread singleton
        if mode == "permanent":
            self._spawn_permanent(svc_name)

    def _mode_for(self, interval_s: float) -> str:
        if interval_s <= 1.0:
            # permanent si sous le seuil, sinon éphémère
            nb = sum(1 for s in self._svcs.values()
                     if s["mode"] == "permanent")
            return "permanent" if nb < SEUIL_SECONDE_PERMANENT else "ephémere"
        return "ephémere"

    def unregister(self, svc_name: str) -> None:
        svc = self._svcs.pop(svc_name, None)
        if svc and svc["thread"] and svc["thread"].is_alive():
            pass  # daemon : il mourra seul
        _handlers.pop(svc_name, None)
        table = self._table_for(svc["mode"]) if svc else "service_ticks"
        try:
            self.conn.execute(
                f"DELETE FROM {table} WHERE svc_name = ?", (svc_name,))
            self.conn.commit()
        except Exception:
            pass

    def load_persisted(self) -> int:
        """Recharge les services enregistrés (tables BDD) dans le cache local.
        À appeler au démarrage pour reprendre les services persistés (les
        callables sont résolus par cmd au premier tick)."""
        n = 0
        for table, mode in (("service_ticks", "ephémere"),
                            ("service_ticks_secondes", "permanent")):
            try:
                rows = self.conn.execute(
                    f"SELECT svc_name, cmd, enabled FROM {table} "
                    f"WHERE enabled = 1").fetchall()
            except Exception:
                continue
            for r in rows:
                name = r["svc_name"]
                interval = 1.0 if mode == "permanent" else 60.0
                with self._lock:
                    self._svcs.setdefault(name, {
                        "mode": mode, "interval": interval,
                        "next_tick": 0.0, "starts": 0, "thread": None,
                        "run_id": None, "fn": None,
                    })
                    heapq.heappush(self._heap, (0.0, name))
                n += 1
        return n

    def list(self) -> List[dict]:
        out = []
        for name, svc in sorted(self._svcs.items()):
            t = svc.get("thread")
            out.append({"svc_name": name, "mode": svc["mode"],
                        "interval_s": svc["interval"],
                        "next_tick": svc["next_tick"],
                        "starts": svc["starts"],
                        "running": bool(t and t.is_alive()),
                        "thread_id": t.ident if t else None})
        return out

    # ── Résolution du callable ──────────────────────────────

    def _resolve(self, name: str) -> Optional[Callable[[], Any]]:
        if name in _handlers:
            return _handlers[name]
        # pas de fn locale : essayer via cmd en BDD
        table = self._table_for(self._svcs[name]["mode"]) \
            if name in self._svcs else "service_ticks"
        try:
            row = self.conn.execute(
                f"SELECT cmd FROM {table} WHERE svc_name = ?", (name,)).fetchone()
        except Exception:
            return None
        if not row or not row["cmd"]:
            return None
        cmd = row["cmd"]
        if ":" in cmd:
            mod, fname = cmd.split(":", 1)
            try:
                return getattr(importlib.import_module(mod), fname)
            except Exception:
                return None
        return None

    # ── Threads éphémères (avec id + vérification) ──────────

    def _spawn_ephémere(self, name: str) -> None:
        """Lance un thread éphémère ; crée la ligne service_tick_runs avec
        thread_id (ident). Le thread s'auto-retire à la fin."""
        svc = self._svcs[name]
        fn = svc["fn"] or self._resolve(name)
        if fn is None:
            return
        with self._lock:
            run_id = None
            try:
                cur = self.conn.execute(
                    "INSERT INTO service_tick_runs (svc_name, started_at, status) "
                    "VALUES (?, ?, 'running')", (name, time.time()))
                self.conn.commit()
                run_id = cur.lastrowid
            except Exception:
                pass
            svc["run_id"] = run_id
            svc["starts"] += 1
            svc["last_started"] = time.time()

        def run():
            t0 = time.time()
            try:
                fn()
            except Exception:
                pass
            finally:
                dur = time.time() - t0
                with self._lock:
                    svc["thread"] = None
                    svc["next_tick"] = time.time() + svc["interval"]
                    if run_id:
                        try:
                            self.conn.execute(
                                "UPDATE service_tick_runs SET status = 'done', "
                                "finished_at = ?, duration_s = ? WHERE run_id = ?",
                                (time.time(), dur, run_id))
                            self.conn.commit()
                        except Exception:
                            pass
                    self._flush(name, last_launch=t0, last_duration_s=dur,
                                running=0)

        t = threading.Thread(target=run, name=f"tick-{name}", daemon=True)
        with self._lock:
            svc["thread"] = t
        t.start()
        # enregistre le thread_id (ident) une fois le thread démarré
        if run_id:
            try:
                self.conn.execute(
                    "UPDATE service_tick_runs SET thread_id = ? WHERE run_id = ?",
                    (t.ident, run_id))
                self.conn.commit()
            except Exception:
                pass

    # ── Threads permanents (1s, singleton boucle) ────────────

    def _spawn_permanent(self, name: str) -> None:
        """Un singleton qui BOUCLE : while: fn(); sleep(interval). Le ticker
        ne le relance que s'il est mort."""
        svc = self._svcs[name]
        fn = svc["fn"] or self._resolve(name)
        if fn is None:
            return
        interval = max(0.1, svc["interval"])

        def loop():
            with self._lock:
                svc["next_tick"] = time.time() + interval
            while not self._stop.is_set():
                t0 = time.time()
                try:
                    fn()
                except Exception:
                    pass
                finally:
                    dur = time.time() - t0
                    with self._lock:
                        self._flush(name, last_launch=t0, last_duration_s=dur,
                                    running=1)
                self._stop.wait(interval)

        t = threading.Thread(target=loop, name=f"tick-perm-{name}", daemon=True)
        with self._lock:
            svc["thread"] = t
            svc["starts"] += 1
        t.start()

    # ── Ticker maître ────────────────────────────────────────

    def tick(self) -> dict:
        """Un passage : lance les éphémères échus, vérifie les threads
        (is_alive), détecte les timeouts (10 échéances)."""
        launched, running, timedout = [], 0, []
        now = time.time()
        due: List[str] = []
        relaunch_perm: List[str] = []
        with self._lock:
            # nettoie la heap des entrées obsolètes, récupère les échus
            while self._heap:
                nt, name = self._heap[0]
                if name not in self._svcs:
                    heapq.heappop(self._heap)
                    continue
                svc = self._svcs[name]
                if svc["mode"] == "permanent":
                    heapq.heappop(self._heap)
                    t = svc["thread"]
                    if t is None or not t.is_alive():
                        relaunch_perm.append(name)  # spawn hors lock
                    else:
                        running += 1
                    continue
                if nt <= now:
                    heapq.heappop(self._heap)
                    due.append(name)
                else:
                    break  # heap triée : plus rien d'échu
            # vérifie les éphémères déjà lancés : is_alive + timeout
            for name, svc in list(self._svcs.items()):
                if svc["mode"] != "ephémere":
                    continue
                t = svc["thread"]
                if t is not None and t.is_alive():
                    running += 1
                    if svc["starts"] > 0:
                        run_id = svc.get("run_id")
                        age = time.time() - svc.get("last_started", now)
                        if age > TIMEOUT_TICKS * svc["interval"]:
                            timedout.append(name)
                            try:
                                self.conn.execute(
                                    "UPDATE service_tick_runs SET status = 'timedout' "
                                    "WHERE run_id = ?", (run_id,))
                                self.conn.commit()
                            except Exception:
                                pass
                            # détaché → relancé au prochain tick (échéance passée)
                            svc["thread"] = None
                            svc["next_tick"] = 0.0
                            heapq.heappush(self._heap, (0.0, name))
        # ── hors du lock : lance les échus + relance les permanents morts ──
        for name in relaunch_perm:
            self._spawn_permanent(name)
            launched.append(name)
        for name in due:
            svc = self._svcs[name]
            if svc["thread"] is not None and svc["thread"].is_alive():
                continue  # déjà en cours (singleton)
            self._spawn_ephémere(name)
            launched.append(name)
        return {"launched": launched, "running": running,
                "timedout": timedout}

    # ── Ticker maître / arrêt ───────────────────────────────

    def start(self, tick_s: float = DEFAULT_TICK) -> None:
        self._stop.clear()
        threading.Thread(target=self._loop, args=(tick_s,),
                         name="service-ticker", daemon=True).start()

    def _loop(self, tick_s: float) -> None:
        while not self._stop.wait(tick_s):
            try:
                self.tick()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()
        for svc in self._svcs.values():
            t = svc.get("thread")
            if t and t.is_alive():
                t.join(timeout=2)


def run_cli() -> None:
    st = ServiceTicker()
    st.start()
    print("service_ticker démarré (tick maître 1s). Ctrl-C pour arrêter.")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        st.stop()


if __name__ == "__main__":
    run_cli()
