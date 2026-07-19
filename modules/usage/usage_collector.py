"""Rassembleur d'usage — consolide le journal disque dans SQLite.

Processe séparé (lancé par le daemon via start_new_session, ou manuellement).
Boucle :
  1. rename atomique real_call.log -> real_call.log.flush (evite la perte
     pendant le traitement : les nouveaux appels continuent dans .log).
  2. pour chaque ligne : INSERT real_call_models + upsert endpoint_model_usage
     + degrade key_endpoint_models.available si echec.
  3. calcul du cout USD via provider_models.cost_per_*.
  4. en cas de lock SQLite -> rename en .retry, retry au cycle suivant.
  5. rotation d'archive si > MAX_ARCHIVE_BYTES.

Lecture de la config RAM/HD depuis modules.usage.usage_log (globals centraux).
"""

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# Permet l'execution standalone (python modules/usage/usage_collector.py)
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

from modules.usage import usage_log  # noqa: E402
from modules.sql.db import ModelWeaverDB, CatalogueDB  # noqa: E402

POLL_INTERVAL = 5.0          # secondes entre deux cycles (override via config)
FLUSH_SUFFIX = ".flush"
RETRY_SUFFIX = ".retry"


def _poll_interval() -> float:
    try:
        from modules.config.config_manager import config
        return float(config.get("usage.collector_poll_seconds", POLL_INTERVAL))
    except Exception:
        return POLL_INTERVAL


def _agent_actif_max_rows() -> int:
    try:
        from modules.config.config_manager import config
        return int(config.get("usage.agent_actif_max_rows", 1000))
    except Exception:
        return 1000


def _cost_for(cat, provider_ref: str, model_ref: str,
              tokens_in: int, tokens_out: int) -> float:
    """Cout USD estime via provider_models.cost_per_input/output_token.
    Retourne 0.0 si tarifs manquants (NULL). Best-effort."""
    try:
        row = cat.conn.execute("""
            SELECT pm.cost_per_input_token, pm.cost_per_output_token
            FROM provider_models pm
            JOIN catalogue_providers p ON p.id = pm.provider_id
            JOIN catalogue_models m ON m.id = pm.model_id
            WHERE p.ref = ? AND m.ref = ?
            LIMIT 1
        """, (provider_ref, model_ref)).fetchone()
        if not row:
            return 0.0
        cin, cout = row["cost_per_input_token"], row["cost_per_output_token"]
        if cin is None and cout is None:
            return 0.0
        try:
            cin = float(cin) if cin is not None else 0.0
            cout = float(cout) if cout is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
        return cin * tokens_in + cout * tokens_out
    except Exception:
        return 0.0


def _degrade_available(cat, endpoint_id: Optional[int], model_ref: str) -> None:
    """Marque le modele injoignable (sera re-upgrade au refresh)."""
    if endpoint_id is None:
        return
    try:
        cat.conn.execute("""
            UPDATE key_endpoint_models SET available = 0
            WHERE endpoint_id = ? AND model_id = (
                SELECT id FROM catalogue_models WHERE ref = ?
            )
        """, (endpoint_id, model_ref))
    except Exception:
        pass


def _consume_file(path: Path, mw: ModelWeaverDB, cat: CatalogueDB) -> int:
    """Lit + consolide un fichier de lignes JSON. Retourne le nb de lignes
    traitees. Leve sur lock SQLite (appele dans try/except par le caller)."""
    count = 0
    conn = mw.conn
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            provider_ref = rec.get("provider_ref", "")
            model_ref = rec.get("model_ref", "")
            status = rec.get("status", "ok")
            endpoint_id = rec.get("endpoint_id")
            tokens_in = int(rec.get("tokens_in", 0) or 0)
            tokens_out = int(rec.get("tokens_out", 0) or 0)
            # cout : priorite a la valeur deja calculee, sinon on la deduit.
            cost = float(rec.get("cost") or 0.0)
            if cost == 0.0:
                cost = _cost_for(cat, provider_ref, model_ref, tokens_in, tokens_out)

            conn.execute("""
                INSERT INTO real_call_models
                    (provider_ref, endpoint_id, key_ref, model_ref, agent_id,
                     sent_at, received_at, tokens_in, tokens_out, cost,
                     status, error_code, error_detail, window_key)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                provider_ref, endpoint_id, rec.get("key_ref"),
                model_ref, rec.get("agent_id"),
                rec.get("sent_at"), rec.get("received_at"),
                tokens_in, tokens_out, cost, status,
                rec.get("error_code"), rec.get("error_detail"),
                rec.get("window_key"),
            ))

            # upsert endpoint_model_usage
            cur = conn.execute(
                "SELECT id FROM endpoint_model_usage WHERE model_ref = ? "
                "ORDER BY id DESC LIMIT 1", (model_ref,))
            row = cur.fetchone()
            working = 1 if status == "ok" else 0
            if row:
                conn.execute("""
                    UPDATE endpoint_model_usage SET
                        requests = requests + 1,
                        tokens_in = tokens_in + ?,
                        tokens_out = tokens_out + ?,
                        cost = cost + ?,
                        last_call_at = ?,
                        last_call_working = ?,
                        error_count = error_count + ?
                    WHERE id = ?
                """, (tokens_in, tokens_out, cost, rec.get("received_at"),
                      working, 0 if status == "ok" else 1, row["id"]))
            else:
                conn.execute("""
                    INSERT INTO endpoint_model_usage
                        (endpoint_id, model_ref, agent_id, requests, tokens_in,
                         tokens_out, cost, last_call_at, last_call_working, error_count)
                    VALUES (?,?,?,1,?,?,?,?,?,?)
                """, (endpoint_id, model_ref, rec.get("agent_id"),
                      tokens_in, tokens_out, cost, rec.get("received_at"),
                      working, 0 if status == "ok" else 1))

            if status != "ok":
                _degrade_available(cat, endpoint_id, model_ref)
            count += 1
    conn.commit()
    cat.conn.commit()
    return count


def _rotate_archives(log_dir: Path) -> None:
    """Si le fichier .flush depasse MAX_ARCHIVE_BYTES(), on l'archive en
    real_call.log.N (N = prochain libre), et on nettoie les trop vieux."""
    flush = log_dir / (usage_log.LOG_FILENAME + FLUSH_SUFFIX)
    if not flush.exists():
        return
    try:
        if flush.stat().st_size < usage_log.MAX_ARCHIVE_BYTES():
            return
    except Exception:
        return
    # trouver le plus grand index d'archive existant
    max_idx = 0
    for p in log_dir.glob(usage_log.ARCHIVE_PREFIX + "*"):
        try:
            idx = int(p.name[len(usage_log.ARCHIVE_PREFIX):])
            max_idx = max(max_idx, idx)
        except ValueError:
            continue
    dest = log_dir / (usage_log.ARCHIVE_PREFIX + str(max_idx + 1))
    try:
        os.replace(str(flush), str(dest))
    except Exception:
        pass
    # nettoyer les archives au-dela de MAX_ARCHIVES()
    archives = sorted(
        [p for p in log_dir.glob(usage_log.ARCHIVE_PREFIX + "*")],
        key=lambda p: p.name,
    )
    while len(archives) > usage_log.MAX_ARCHIVES():
        old = archives.pop(0)
        try:
            old.unlink()
        except Exception:
            pass


def _enforce_agent_actif_cap(mw: ModelWeaverDB) -> None:
    """Borne la table agent_actif (FIFO) : si elle depasse le max, on supprime
    les plus anciens (dernier heartbeat le plus vieux). Borne configurable via
    config usage.agent_actif_max_rows."""
    cap = _agent_actif_max_rows()
    try:
        cur = mw.conn.execute("SELECT COUNT(*) FROM agent_actif").fetchone()[0]
        if cur <= cap:
            return
        # supprime les (cur - cap) agents les moins recents en heartbeat
        mw.conn.execute("""
            DELETE FROM agent_actif
            WHERE agent_id IN (
                SELECT agent_id FROM agent_actif
                ORDER BY last_heartbeat ASC
                LIMIT ?
            )
        """, (cur - cap,))
        mw.conn.commit()
    except Exception:
        pass


def run_once() -> int:
    """Un cycle de consolidation. Retourne le nb de lignes traitees."""
    log_dir = Path(os.path.expanduser("~")) / ".modelweaver" / usage_log.USAGE_DIR
    log_path = log_dir / usage_log.LOG_FILENAME
    flush_path = log_dir / (usage_log.LOG_FILENAME + FLUSH_SUFFIX)
    retry_path = log_dir / (usage_log.LOG_FILENAME + RETRY_SUFFIX)

    if not log_path.exists() and not retry_path.exists():
        return 0

    total = 0
    mw = ModelWeaverDB()
    cat = CatalogueDB()
    try:
        # 1) basculer le journal courant en fichier a traiter (atomique)
        if log_path.exists() and log_path.stat().st_size > 0:
            try:
                os.replace(str(log_path), str(flush_path))
            except Exception:
                pass
        # 2) traiter le fichier retry precedent (si lock au cycle d'avant)
        if retry_path.exists():
            try:
                total += _consume_file(retry_path, mw, cat)
                retry_path.unlink()
            except Exception:
                # toujours locked -> on laisse en .retry pour le prochain cycle
                return total
        # 3) traiter le flush frais
        if flush_path.exists():
            try:
                total += _consume_file(flush_path, mw, cat)
                flush_path.unlink()
            except Exception:
                # lock SQLite : renommer en .retry pour rejeu differe
                try:
                    os.replace(str(flush_path), str(retry_path))
                except Exception:
                    pass
        # 4) rotation d'archive si besoin
        _rotate_archives(log_dir)
        # 5) borne taille table agent_actif (FIFO)
        _enforce_agent_actif_cap(mw)
    finally:
        mw.close()
        cat.close()
    return total


def main() -> None:
    print(f"usage_collector demarre (poll={POLL_INTERVAL}s, "
          f"archive_max={usage_log.MAX_ARCHIVE_BYTES()//(1024*1024)}Mo, "
          f"ram_max={usage_log.MAX_RAM_BUFFER_BYTES()//(1024*1024)}Mo)")
    try:
        while True:
            try:
                n = run_once()
                if n:
                    print(f"  [{time.strftime('%H:%M:%S')}] {n} appel(s) consolide(s)")
            except Exception as e:
                print(f"  erreur cycle: {e}")
            time.sleep(_poll_interval())
    except KeyboardInterrupt:
        print("usage_collector arrete")


if __name__ == "__main__":
    main()
