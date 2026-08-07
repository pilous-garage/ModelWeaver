"""usage_batcher — ticker de batchage de la consommation LLM, piloté par les DONNÉES.

Le ticker n'utilise PAS l'heure de sa propre exécution pour décider quoi agréger :
il se fie au timestamp des données (model_call_log.created_at). Principe :

  1. frontier = MAX(created_at) de model_call_log  (la donnée la plus récente)
  2. cutoff = frontier - BATCH_MARGIN  (5 min avant la plus récente)
     → on n'agrège/supprime QUE les appels dont created_at <= cutoff, pour ne
       JAMAIS rattraper un call encore en cours d'écriture par le bridge
       (désynchronisation ticker/bridge : multimachine, erreur, latence…).
  3. Batch 1m : les appels détaillés (created_at <= cutoff) sont agrégés par
     bucket de 60 s vers usage_history_1m (upsert), puis supprimés de
     model_call_log (le détail < 5 min reste pour recent_llm).
  4. CASCADE : chaque niveau supérieur (15m → 3h → 1d → 1w → 1mo) agrège le
     niveau précédent pour les buckets dont la fenêtre est entièrement
     <= cutoff, puis purge ces buckets du niveau source.

Le streaming est loggé 1 ligne/appel (call_type=chat_stream) dans
model_call_log → COUNT(*) du batch = nb d'appels réel, pas de perte.

Singleton flock (comme usage_collector). Boucle configurable (défaut 60 s).
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Marge de sécurité : on ne batch jamais les appels de moins de 5 min (le
# bridge peut encore écrire / un call streaming est en cours).
BATCH_MARGIN_SECONDS = 300
POLL_INTERVAL_SECONDS = 60

# Cascade : (table_source, table_cible, taille_bucket_secondes, rétention).
# Chaque entrée agrège la table précédente vers la suivante, buckets périmés.
CASCADE = [
    ("usage_history_1m",   "usage_history_15m", 15 * 60, 24 * 3600),
    ("usage_history_15m",  "usage_history_3h",  3 * 3600, 7 * 24 * 3600),
    ("usage_history_3h",   "usage_history_1d",  24 * 3600, 30 * 24 * 3600),
    ("usage_history_1d",   "usage_history_1w",  7 * 24 * 3600, 180 * 24 * 3600),
    ("usage_history_1w",   "usage_history_1mo", 30 * 24 * 3600, 2 * 365 * 24 * 3600),
]
# La table 1m est conservée 24h (détail récent), les suivantes plus longtemps.
# Rétention par niveau (source) : on purge les buckets < now - retention.

_COLS = ("requests", "tokens_in", "tokens_out", "tokens_thinking", "cost")


def _rt_conn():
    """Connexion au runtime DB (usage_history_*) + catalogue (model_call_log)."""
    from modules.sql.db import RuntimeDB
    return RuntimeDB()


def _cat_conn():
    from modules.sql.db import CatalogueDB
    return CatalogueDB()


def _data_frontier(cat) -> Optional[int]:
    """La donnée la plus récente de model_call_log (source de vérité)."""
    try:
        row = cat.conn.execute("SELECT MAX(created_at) m FROM model_call_log").fetchone()
        return int(row["m"]) if row and row["m"] else None
    except Exception:
        return None


def _batch_1m(cat, rt) -> int:
    """Agrège les appels détaillés (<= cutoff) en buckets 1 min vers usage_history_1m,
    puis supprime ces lignes détaillées de model_call_log.

    model_call_log vit dans le CATALOGUE db ; usage_history_1m dans le RUNTIME
    db. Deux connexions distinctes : on lit les agrégats sur cat, on les upsert
    dans rt, puis on delete sur cat.

    Retourne le nombre de lignes détaillées traitées.
    """
    frontier = _data_frontier(cat)
    if frontier is None:
        return 0
    cutoff = frontier - BATCH_MARGIN_SECONDS
    try:
        rows = cat.conn.execute("""
            SELECT
                CAST(l.created_at / 60 AS INTEGER) * 60 AS bucket,
                COALESCE(p.ref, '') AS provider_ref,
                COALESCE(m.ref, '') AS model_ref,
                COALESCE(l.agent_id, '') AS agent_id,
                COUNT(*) AS requests,
                SUM(l.tokens_in) AS tokens_in,
                SUM(l.tokens_out) AS tokens_out,
                SUM(l.tokens_thinking) AS tokens_thinking,
                MIN(l.created_at) AS first_call,
                MAX(l.created_at) AS last_call
            FROM model_call_log l
            LEFT JOIN catalogue_providers p ON p.id = l.provider_id
            LEFT JOIN catalogue_models m ON m.id = l.model_id
            WHERE l.created_at <= ?
            GROUP BY 1, 2, 3, 4
        """, (cutoff,)).fetchall()
        if not rows:
            return 0
        for r in rows:
            rt.conn.execute("""
                INSERT INTO usage_history_1m
                    (bucket, provider_ref, model_ref, agent_id,
                     requests, tokens_in, tokens_out, tokens_thinking, cost,
                     first_call, last_call)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0.0, ?, ?)
                ON CONFLICT(bucket, provider_ref, model_ref, agent_id) DO UPDATE SET
                    requests = usage_history_1m.requests + excluded.requests,
                    tokens_in = usage_history_1m.tokens_in + excluded.tokens_in,
                    tokens_out = usage_history_1m.tokens_out + excluded.tokens_out,
                    tokens_thinking = usage_history_1m.tokens_thinking + excluded.tokens_thinking,
                    cost = usage_history_1m.cost + excluded.cost,
                    last_call = MAX(usage_history_1m.last_call, excluded.last_call),
                    first_call = MIN(usage_history_1m.first_call, excluded.first_call)
            """, (r["bucket"], r["provider_ref"], r["model_ref"], r["agent_id"],
                  r["requests"] or 0, r["tokens_in"] or 0, r["tokens_out"] or 0,
                  r["tokens_thinking"] or 0, r["first_call"], r["last_call"]))
        rt.conn.commit()
        # ── Séquences de réussite par (provider, model) ──
        # Pour chaque modèle ayant des appels dans la fenêtre :
        #   - succès → ouvre/continue la séquence open (cumule req/tok/latence)
        #   - échec  → clôture la séquence open (seq_end, duration_s, closed)
        try:
            seq_rows = cat.conn.execute("""
                SELECT COALESCE(p.ref, '?') AS provider_ref,
                       COALESCE(m.ref, '?') AS model_ref,
                       SUM(CASE WHEN l.success = 1 THEN 1 ELSE 0 END) AS ok,
                       SUM(CASE WHEN l.success = 0 THEN 1 ELSE 0 END) AS ko,
                       SUM(CASE WHEN l.success = 1 THEN l.tokens_in ELSE 0 END) AS tok_in,
                       SUM(CASE WHEN l.success = 1 THEN l.tokens_out ELSE 0 END) AS tok_out,
                       MIN(CASE WHEN l.success = 1 THEN l.created_at END) AS first_ok,
                       MAX(CASE WHEN l.success = 1 THEN l.created_at END) AS last_ok,
                       MAX(CASE WHEN l.success = 0 THEN l.created_at END) AS last_ko,
                       AVG(CASE WHEN l.success = 1 THEN l.latency_ms END) AS avg_lat
                FROM model_call_log l
                LEFT JOIN catalogue_providers p ON p.id = l.provider_id
                LEFT JOIN catalogue_models m ON m.id = l.model_id
                WHERE l.created_at <= ?
                GROUP BY p.ref, m.ref
            """, (cutoff,)).fetchall()
            for s in seq_rows:
                prov, model = s["provider_ref"], s["model_ref"]
                ok, ko = s["ok"] or 0, s["ko"] or 0
                # Séquence open existante ?
                run = rt.conn.execute(
                    "SELECT id, seq_start, requests, tokens_in, tokens_out, "
                    "avg_latency_ms FROM model_success_runs "
                    "WHERE provider_ref = ? AND model_ref = ? AND status = 'open' "
                    "ORDER BY id DESC LIMIT 1",
                    (prov, model)).fetchone()
                run_id = run["id"] if run else None
                if ok > 0:
                    # Cumuler les succès dans la séquence open (ou en ouvrir une).
                    n = ok
                    tin = s["tok_in"] or 0
                    tout = s["tok_out"] or 0
                    avg = s["avg_lat"]
                    if run_id:
                        rt.conn.execute("""
                            UPDATE model_success_runs SET
                                requests = requests + ?,
                                tokens_in = tokens_in + ?,
                                tokens_out = tokens_out + ?,
                                avg_latency_ms = CASE WHEN requests + ? > 0 THEN
                                    ((avg_latency_ms * requests) + ?) / (requests + ?) ELSE 0 END,
                                updated_at = strftime('%s','now')
                            WHERE id = ?
                        """, (n, tin, tout, n, avg or 0, n, run_id))
                    else:
                        cur = rt.conn.execute("""
                            INSERT INTO model_success_runs
                                (provider_ref, model_ref, seq_start, requests,
                                 tokens_in, tokens_out, avg_latency_ms, status)
                            VALUES (?, ?, ?, ?, ?, ?, ?, 'open')
                        """, (prov, model, s["first_ok"] or int(time.time()),
                              n, tin, tout, avg or 0))
                        run_id = cur.lastrowid if hasattr(cur, "lastrowid") else None
                if ko > 0 and run_id:
                    # Clôturer la séquence open (l'échec met fin au run de succès),
                    # même si elle vient d'être ouverte dans ce batch.
                    rt.conn.execute("""
                        UPDATE model_success_runs SET
                            seq_end = ?, duration_s = ? - seq_start,
                            status = 'closed', updated_at = strftime('%s','now')
                        WHERE id = ?
                    """, (s["last_ko"], s["last_ko"], run_id))
            rt.conn.commit()
        except Exception:
            try:
                rt.conn.rollback()
            except Exception:
                pass
        # Supprimer les lignes détaillées batchées (idempotent).
        del_cur = cat.conn.execute(
            "DELETE FROM model_call_log WHERE created_at <= ?", (cutoff,))
        n_del = del_cur.rowcount if hasattr(del_cur, "rowcount") else 0
        cat.conn.commit()
        return n_del
    except Exception:
        try:
            cat.conn.rollback()
            rt.conn.rollback()
        except Exception:
            pass
        return 0


def _cascade(cat, rt, frontier: int) -> int:
    """Agrège chaque niveau vers le supérieur pour les buckets périmés, puis purge.

    Un bucket du niveau source est "périmé" si sa fenêtre entière est <= frontier
    (toute la période est connue) ET <= cutoff de sécurité (5 min avant la plus
    récente). On agrège alors vers le niveau cible (bucket tronqué à la taille du
    niveau) et on supprime le bucket source.
    """
    cutoff = frontier - BATCH_MARGIN_SECONDS
    n = 0
    for src, dst, bucket_s, _keep in CASCADE:
        try:
            # Agrège les buckets source dont la fenêtre [bucket, bucket+bucket_s)
            # est entièrement <= cutoff (donc figée).
            cur = rt.conn.execute(f"""
                INSERT INTO {dst}
                    (bucket, provider_ref, model_ref, agent_id,
                     requests, tokens_in, tokens_out, tokens_thinking, cost,
                     first_call, last_call)
                SELECT
                    CAST(s.bucket / {bucket_s} AS INTEGER) * {bucket_s},
                    COALESCE(s.provider_ref, ''), COALESCE(s.model_ref, ''),
                    COALESCE(s.agent_id, ''),
                    SUM(s.requests), SUM(s.tokens_in), SUM(s.tokens_out),
                    SUM(s.tokens_thinking), SUM(s.cost),
                    MIN(s.first_call), MAX(s.last_call)
                FROM {src} s
                WHERE s.bucket + {bucket_s} <= ?
                GROUP BY 1, 2, 3, 4
                ON CONFLICT(bucket, provider_ref, model_ref, agent_id) DO UPDATE SET
                    requests = {dst}.requests + excluded.requests,
                    tokens_in = {dst}.tokens_in + excluded.tokens_in,
                    tokens_out = {dst}.tokens_out + excluded.tokens_out,
                    tokens_thinking = {dst}.tokens_thinking + excluded.tokens_thinking,
                    cost = {dst}.cost + excluded.cost,
                    last_call = MAX({dst}.last_call, excluded.last_call),
                    first_call = MIN({dst}.first_call, excluded.first_call)
            """, (cutoff,))
            n += cur.rowcount if hasattr(cur, "rowcount") else 0
            # Purge des buckets source agrégés.
            rt.conn.execute(
                f"DELETE FROM {src} WHERE bucket + {bucket_s} <= ?", (cutoff,))
        except Exception:
            try:
                rt.conn.rollback()
            except Exception:
                pass
    try:
        rt.conn.commit()
    except Exception:
        pass
    return n


def _purge_expired(rt) -> int:
    """Purge TTL des tables d'agrégats (par rétention de chaque niveau)."""
    now = int(time.time())
    n = 0
    keeps = {
        "usage_history_1m": 24 * 3600,
        "usage_history_15m": 7 * 24 * 3600,
        "usage_history_3h": 30 * 24 * 3600,
        "usage_history_1d": 180 * 24 * 3600,
        "usage_history_1w": 2 * 365 * 24 * 3600,
        "usage_history_1mo": 5 * 365 * 24 * 3600,
    }
    for tbl, keep in keeps.items():
        try:
            cur = rt.conn.execute(
                f"DELETE FROM {tbl} WHERE bucket < ?", (now - keep,))
            n += cur.rowcount if hasattr(cur, "rowcount") else 0
        except Exception:
            pass
    try:
        rt.conn.commit()
    except Exception:
        pass
    return n


def run_once() -> Dict[str, Any]:
    """Un cycle de batchage : retourne {batched, cascade, purged, frontier}."""
    cat = _cat_conn()
    rt = _rt_conn()
    frontier = _data_frontier(cat)
    if frontier is None:
        return {"batched": 0, "cascade": 0, "purged": 0, "frontier": None}
    batched = _batch_1m(cat, rt)
    cascaded = _cascade(cat, rt, frontier)
    purged = _purge_expired(rt)
    return {"batched": batched, "cascade": cascaded, "purged": purged,
            "frontier": frontier}


def _acquire_singleton() -> Optional[object]:
    """Garde SINGLETON via flock sur un pidfile (comme usage_collector)."""
    import fcntl
    pidfile = Path.home() / ".modelweaver" / "run" / "usage_batcher.lock"
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    fh = open(pidfile, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        fh.close()
        return None
    return fh


def _poll_interval() -> float:
    try:
        from modules.config.config_manager import config
        return float(config.get("usage.batcher_poll_seconds", POLL_INTERVAL_SECONDS))
    except Exception:
        return POLL_INTERVAL_SECONDS


def main() -> None:
    lock = _acquire_singleton()
    if lock is None:
        print("usage_batcher deja en cours — abandon (singleton)")
        sys.exit(0)
    print(f"usage_batcher demarre (poll={POLL_INTERVAL_SECONDS}s, "
          f"margin={BATCH_MARGIN_SECONDS}s, cascade={len(CASCADE)} niveaux)")
    try:
        while True:
            try:
                r = run_once()
                if r.get("batched") or r.get("cascade"):
                    print(f"  [{time.strftime('%H:%M:%S')}] batch={r['batched']} "
                          f"cascade={r['cascade']} purge={r['purged']}")
            except Exception as e:
                print(f"  erreur cycle: {e}")
            time.sleep(_poll_interval())
    except KeyboardInterrupt:
        print("usage_batcher arrete")


if __name__ == "__main__":
    main()
