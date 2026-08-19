"""writer_dedie — socle commun des writers dédiés (domaines à token).

Règle (cf. docs/migration_sql.md) : UN domaine = UN writer dédié, et le
writer dédié n'existe QUE pour les domaines qui fonctionnent par BATCHS DE
LIGNES (assurer qu'un seul writer prend le lock et écrit par mini-batchs) :
`local`, `buffer`, `batch`, `score`, `info_llm`.

Chaque domaine a son `modules/sqlite/<domaine>/writer_dedie.py` : une
SURCLASSE concrète de `WriterDedie` qui définit `run_once()` (un cycle de
travail complet), les constantes (batch, marges, TTL) et les helpers métier.
Ce module-ci ne sait RIEN des domaines : mécaniques génériques uniquement.

CONTRAT DE COMMUNICATION (comment les autres lui demandent quoi écrire) :
  - Les autres domaines ne l'appellent JAMAIS directement : ils DÉPOSENT
    dans sa file d'entrée (buffer_op pending, model_call_log,
    local_catalogue…) et le writer consomme (flush + marque + purge).
  - Cas rare d'autorisation croisée : `cross_writer(domaine, token)` donne
    un writer d'UN AUTRE domaine (ex. le writer local qui marque/ purge la
    file buffer qu'il a fini d'importer). C'est l'EXCEPTION, jamais la règle.

BRANCHEMENT SERVICE_TICK :
  - `tick()` (= run_once sans argument) est le point d'entrée.
  - `register(ticker, ...)` l'enregistre avec cmd de reprise
    `modules.sqlite.<dom>.writer_dedie:tick`.
  - `main()` = mode autonome (CLI) avec flock singleton.
"""

from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from modules.sqlite.base import Db

DEFAULT_BATCH = 1000


class WriterDedie:
    """Base des writers dédiés. Surcharger DOMAIN/TOKEN + implémenter run_once()."""

    # ── attributs à surcharger par le domaine ──────────────────────────────
    DOMAIN: str = ""                # ex. "batch" → modules.sqlite.batch
    TOKEN: str = ""                 # ex. "write_batch" → WRITE_*_TOKEN

    def __init__(self, batch: int = DEFAULT_BATCH, token: str = "",
                 log: Optional[Callable] = None):
        self.batch = batch
        self.token = token or self.TOKEN
        self._log = log or (lambda *a: None)

    # ── accès domaine / cross-domaine ─────────────────────────────────────
    def _domain(self):
        return importlib.import_module(f"modules.sqlite.{self.DOMAIN}")

    def reader(self) -> Db:
        """db_ro du domaine (lecture de ses sources)."""
        return self._domain().db_ro()

    def writer(self) -> Db:
        """get_writer du domaine (token lié)."""
        return self._domain().get_writer(self.token)

    def cross_writer(self, domain: str, token: str = "") -> Db:
        """Writer d'UN AUTRE domaine (autorisation croisée rare) : ex. le
        writer local obtient un writer buffer pour marquer/purger sa file."""
        mod = importlib.import_module(f"modules.sqlite.{domain}")
        return mod.get_writer(token or getattr(mod, f"WRITE_{domain.upper()}_TOKEN", ""))

    # ── mini-batchs (mécanique systémique) ────────────────────────────────
    def chunks(self, rows: Iterable[Any], batch: Optional[int] = None) -> Iterable[List[Any]]:
        """Découpe une liste de lignes en mini-batchs (taille paramétrable)."""
        b = batch or self.batch
        buffer: List[Any] = []
        for r in rows:
            buffer.append(r)
            if len(buffer) >= b:
                yield buffer
                buffer = []
        if buffer:
            yield buffer

    def upsert_batches(self, db: Db, table: str, rows: List[Dict[str, Any]],
                       conflict_cols: List[str], token: str = "") -> int:
        """Upsert par mini-batchs : UNE transaction par batch (upsert_many),
        reprise sûre (un batch en échec ne perd que lui). Retourne le nb."""
        tbl = db.table(table)
        n = 0
        for chunk in self.chunks(rows):
            tbl.upsert_many(chunk, conflict_cols, token=token or self.token)
            n += len(chunk)
        return n

    def add_batches(self, db: Db, table: str, rows: List[Dict[str, Any]],
                    token: str = "") -> int:
        """INSERT pur par mini-batchs (une transaction par batch)."""
        tbl = db.table(table)
        n = 0
        for chunk in self.chunks(rows):
            tbl.add(chunk, token=token or self.token)
            n += len(chunk)
        return n

    def require_writer(self, db: Db, token: str = "") -> None:
        """Garde d'écriture : token explicite OU token lié de l'instance."""
        db.check_write(token or self.token)

    # ── file d'entrée : consommer les demandes des autres domaines ────────
    def drain_pending(self, db: Db, table: str, apply_fn: Callable[[Dict[str, Any]], None],
                      where: Optional[Dict[str, Any]] = None, limit: int = 500,
                      order_by: str = "", status_col: str = "status",
                      ok_status: str = "applied", err_status: str = "error") -> Dict[str, Any]:
        """Consomme une file pending ligne par ligne (pattern import_local
        généralisé). `apply_fn(row)` écrit dans le domaine cible ; la ligne
        est marquée ok_status/err_status selon le résultat."""
        rows = db.table(table).select(where=where, order_by=order_by, limit=limit)
        ok_ids, err_ids = [], []
        for row in rows:
            try:
                apply_fn(dict(row))
                ok_ids.append(row["id"])
            except Exception as e:
                self._log(f"drain {table} row {row.get('id')}: {e}")
                err_ids.append(row["id"])
        n_ok = n_err = 0
        if ok_ids and status_col:
            with db.in_write():
                for rid in ok_ids:
                    db.table(table).update({"id": rid}, {status_col: ok_status},
                                           token=self.token)
                n_ok = len(ok_ids)
        if err_ids and status_col:
            for rid in err_ids:
                db.table(table).update({"id": rid}, {status_col: err_status},
                                       token=self.token)
                n_err += 1
        return {"applied": n_ok, "errors": n_err,
                "pending_left": db.table(table).count(where=where) - n_ok - n_err}

    # ── service_tick ──────────────────────────────────────────────────────
    def run_once(self) -> Dict[str, Any]:
        """UN cycle de travail complet du writer (surchargé)."""
        raise NotImplementedError

    def tick(self) -> Dict[str, Any]:
        """Point d'entrée service_tick : fn() sans argument."""
        return self.run_once()

    def register(self, ticker, interval_s: float = 60.0, name: str = "") -> None:
        """Enregistre le writer sur un ServiceTicker (cmd = reprise après
        restart : `modules.sqlite.<dom>.writer_dedie:tick`)."""
        ticker.register(
            name or f"writer_{self.DOMAIN}",
            interval_s=interval_s,
            fn=self.tick,
            cmd=f"modules.sqlite.{self.DOMAIN}.writer_dedie:tick",
        )

    # ── CLI autonome : flock singleton + boucle ───────────────────────────
    def main(self, sleep_s: float = 60.0, once: bool = False) -> None:
        """Mode autonome : un SEUL process (flock), boucle run_once().
        `once=True` : un seul cycle puis exit (utile pour les crons)."""
        import fcntl
        import sys
        pidfile = Path.home() / ".modelweaver" / "run" / f"{self.DOMAIN}_writer.lock"
        pidfile.parent.mkdir(parents=True, exist_ok=True)
        fh = open(pidfile, "a+")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(f"{self.DOMAIN}_writer deja en cours — abandon (singleton)")
            sys.exit(0)
        print(f"{self.DOMAIN}_writer demarre (batch={self.batch}, sleep={sleep_s}s)")
        try:
            while True:
                t0 = time.time()
                try:
                    r = self.run_once()
                    if r:
                        self._log(f"{self.DOMAIN}_writer run_once: {r}")
                except Exception as e:
                    print(f"  erreur cycle: {e}")
                if once:
                    break
                time.sleep(max(0.0, sleep_s - (time.time() - t0)))
        except KeyboardInterrupt:
            print(f"{self.DOMAIN}_writer arrete")
        finally:
            fh.close()


__all__ = ["WriterDedie", "DEFAULT_BATCH"]