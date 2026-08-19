"""flush_tick — purge/archive générique d'une table d'un domaine.

Règle : on ne flush QUE si TOUTES les conditions du tick sont réunies
(autrement on ne touche à rien — les données récentes et les lignes
non-éligibles ne sont JAMAIS virées).

Paramètres du tick :
  • limit_lines     : nombre de lignes au-delà duquel le flush est autorisé
                      (ex. 10 000).
  • tick_duration_s : âge minimum des lignes pour être éligibles (ex. 600s =
                      jamais de flush de données récentes).
  • eligible_sql    : prédicat SQL des lignes SUPPRIMABLES (ex. "received = 1").
                      Les lignes ne le respectant pas restent quoi qu'il arrive.
  • archive         : True → copie dans `archive_<table>` (même schéma +
                      colonne flushed_at) avant suppression ; False → delete sec.
  • anomaly_sqls    : [(nom, sql)] sondes exécutées à chaque run — leurs
                      résultats anormaux (au-delà de zero) sont rapportés
                      pour étude LLM (volumes, queues non traitées, taux de
                      non-reçus vieux, …).
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from modules.sqlite.base import Db

AnomalySql = Tuple[str, str]


class FlushTick:
    def __init__(self, db: Db, table: str,
                 limit_lines: int = 10_000,
                 tick_duration_s: int = 600,
                 eligible_sql: str = "",
                 eligible_params: Sequence[Any] = (),
                 archive: bool = False,
                 anomaly_sqls: Optional[List[AnomalySql]] = None,
                 id_column: str = "dm_id"):
        self._db = db
        self.table = table
        self.id_column = id_column
        self.limit_lines = limit_lines
        self.tick_duration_s = tick_duration_s
        self.eligible_sql = eligible_sql
        self.eligible_params = tuple(eligible_params)
        self.archive = archive
        self.anomaly_sqls = anomaly_sqls or []
        self.archive_table = f"archive_{table}"

    # -- conditions -------------------------------------------------------

    def _row_count(self) -> int:
        r = self._db._conn.execute(
            f"SELECT COUNT(*) AS n FROM {self.table}").fetchone()
        return int(r["n"])

    def _eligible_where(self) -> str:
        """WHERE complet : lignes âgées ET respectant le prédicat. La colonne
        de date est `created_at` (ISO UTC) — textuel, comparable."""
        clauses = [f"created_at <= datetime('now', '-{self.tick_duration_s} "
                   "seconds')"]
        if self.eligible_sql:
            clauses.append(f"({self.eligible_sql})")
        return " AND ".join(clauses)

    def _eligible_ids(self) -> List[int]:
        rows = self._db._conn.execute(
            f"SELECT {self.id_column} AS id FROM {self.table} "
            f"WHERE {self._eligible_where()}", self.eligible_params).fetchall()
        return [r["id"] for r in rows]

    def _anomalies(self) -> List[Dict[str, Any]]:
        out = []
        for name, sql in self.anomaly_sqls:
            try:
                rows = self._db._conn.execute(sql).fetchall()
                if rows:
                    out.append({"name": name,
                                "rows": [dict(r) for r in rows]})
            except Exception:  # noqa: BLE001 — une sonde ne bloque pas le tick
                pass
        return out

    # -- run ----------------------------------------------------------------

    def run(self) -> Dict[str, Any]:
        """Exécute un tick : flush si et seulement si toutes les conditions
        sont réunies. Retourne un rapport {flushed, archived, kept, reason,
        anomalies}."""
        total = self._row_count()
        if total <= self.limit_lines:
            return {"flushed": 0, "archived": 0,
                    "kept": total, "reason": "below_limit",
                    "anomalies": self._anomalies()}
        ids = self._eligible_ids()
        if not ids:
            return {"flushed": 0, "archived": 0,
                    "kept": total, "reason": "none_eligible",
                    "anomalies": self._anomalies()}
        ph = ",".join("?" for _ in ids)
        where = f"{self.id_column} IN ({ph})"
        archived = 0
        if self.archive:
            cur = self._db._conn.execute(
                f"INSERT INTO {self.archive_table} "
                f"SELECT t.*, datetime('now') AS flushed_at FROM "
                f"{self.table} t WHERE {where}", ids)
            archived = cur.rowcount
        cur = self._db._conn.execute(
            f"DELETE FROM {self.table} WHERE {where}", ids)
        flushed = cur.rowcount
        self._db._conn.commit()
        return {"flushed": flushed, "archived": archived,
                "kept": total - flushed, "reason": "flushed",
                "anomalies": self._anomalies()}


def flush_direct_messages(d: Db, archive: bool = True,
                          limit_lines: int = 10_000,
                          tick_duration_s: int = 600) -> Dict[str, Any]:
    """Tick par défaut de direct_message :
    >10k lignes, âge >10 min, éligible = reçu (received=1) — les non-reçus
    ne sont jamais flushés. Archive (archive_direct_message) par défaut."""
    return FlushTick(
        d, "direct_message",
        limit_lines=limit_lines,
        tick_duration_s=tick_duration_s,
        eligible_sql="received = 1",
        archive=archive,
        anomaly_sqls=[
            ("stale_unreceived",
             "SELECT agent_to, COUNT(*) AS n FROM direct_message "
             "WHERE received = 0 AND created_at <= datetime('now', '-1 hour') "
             "GROUP BY agent_to ORDER BY n DESC LIMIT 10"),
            ("volume_last_hour",
             "SELECT COUNT(*) AS n FROM direct_message "
             "WHERE created_at >= datetime('now', '-1 hour')"),
        ],
    ).run()