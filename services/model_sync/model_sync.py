"""Synchroniseur périodique des modèles par clé API.

Processe séparé (lancé par le daemon via start_new_session, ou manuellement).
Boucle (défaut : 1x/h) :

  1. Pour chaque clé API liée à un endpoint (provider_models_mapping) : interroge
     l'API du provider via DirectBridge.list_available_models.
  2. Met à jour provider_models_mapping :
       - modèle présent   -> declared=1, available=1, last_checked_at
       - modèle disparu   -> declared=0, available=0, last_error,
                             defunct=1 (colonne model_probe_state.defunct)
  3. model_probe_state (backoff par endpoint+key+model) :
       - succès   -> reset compteur + backoff, defunct=0
       - API KO   -> consecutive_failures+1, backoff croissant (base 60s,
                     ×2, plafond 24h), defunct après DEFUNCT_THRESHOLD échecs
       - disparu  -> defunct=1 immédiat + backoff long (re-test périodique :
                     s'il revient dans l'API, on le réactive)

Transactions : la connexion est en AUTCOMMIT (isolation_level=None) — chaque
INSERT/UPDATE est une transaction courte et immédiate. Aucune transaction
implicite ne reste ouverte pendant un appel API (source des locks SQLite
"database is locked" chez les autres écrivains).
"""

import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

BACKOFF_BASE_S = 60          # premier backoff après un échec d'API
BACKOFF_FACTOR = 2           # ×2 à chaque échec consécutif
BACKOFF_MAX_S = 24 * 3600    # plafond
DEFUNCT_THRESHOLD = 5        # échecs consécutifs avant defunct=1
DEFUNCT_REPROBE_S = 24 * 3600  # re-test d'un modèle disparu (défaut 24h)
DEFAULT_INTERVAL_H = 1.0     # un cycle toutes les heures


def _interval_seconds() -> float:
    try:
        from modules.config.config_manager import config
        return float(config.get("models.sync_interval_hours",
                                DEFAULT_INTERVAL_H)) * 3600.0
    except Exception:
        return DEFAULT_INTERVAL_H * 3600.0


def _log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _open_cat():
    """Ouvre le catalogue en AUTCOMMIT (transactions courtes, pas de lock).

    L'initialisation (CatalogueDB.__init__ → _ensure_schema) laisse une
    transaction implicite ouverte (DDL/seed, isolation_level="" par défaut) :
    on la ferme explicitement AVANT de passer en autocommit, sinon le lock
    d'écriture WAL reste tenu par ce process pour toute sa vie.
    """
    from modules.sql.db import CatalogueDB
    cat = CatalogueDB()
    try:
        cat.conn.commit()
    except Exception:
        cat.conn.rollback()
    cat.conn.isolation_level = None
    return cat


def _key_groups(cat, mw=None):
    """Groupes (provider_ref, endpoint_id, key_ref) distincts ayant une clé.

    Source : les clés du KeyManager (modelweaver.db/api_keys) croisées avec les
    endpoints du catalogue (provider_endpoints). Couvre aussi les providers qui
    n'ont encore AUCUNE ligne provider_models_mapping (jamais syncés) — sinon ils
    ne seraient jamais interrogés.
    """
    groups = {}
    rows = cat.conn.execute("""
        SELECT DISTINCT p.ref AS provider_ref, kem.endpoint_id, kem.key_ref
        FROM provider_models_mapping kem
        JOIN catalogue_providers p ON p.id = kem.provider_id
        WHERE kem.key_ref IS NOT NULL AND kem.endpoint_id IS NOT NULL
    """).fetchall()
    for r in rows:
        groups[(r["provider_ref"], r["endpoint_id"], r["key_ref"])] = True

    # Providers avec une clé mais sans ligne KEM : on construit le groupe avec
    # l'endpoint par défaut du catalogue (s'il existe).
    if mw is not None:
        try:
            key_rows = mw.conn.execute("""
                SELECT k.ref AS key_ref, p.ref AS provider_ref
                FROM api_keys k
                JOIN providers p ON p.id = k.provider_id
            """).fetchall()
        except Exception:
            key_rows = []
        for kr in key_rows:
            pref = kr["provider_ref"]
            if pref in ("gemini",):
                # alias : le provider gemini de la BDD locale = google
                pref = "google"
            ep = cat.conn.execute("""
                SELECT endpoint_id FROM provider_endpoints
                WHERE provider_id = (SELECT id FROM catalogue_providers WHERE ref = ?)
                  AND is_default = 1
                LIMIT 1
            """, (pref,)).fetchone()
            if ep is None:
                # provider inconnu du catalogue : chercher un endpoint par URL par défaut
                continue
            groups.setdefault((pref, ep["endpoint_id"], kr["key_ref"]), True)

    return [(p, e, k) for (p, e, k) in groups]


def _upsert_probe_state(cat, endpoint_id: int, key_ref: str,
                        provider_ref: str, model_id: int,
                        ok: bool, absent: bool = False,
                        error: str = "") -> None:
    """Met à jour model_probe_state pour (endpoint_id, key_ref, model_id).

    ok=True     -> succès : reset compteur/backoff, defunct=0
    absent=True -> disparition CONFIRMÉE du listing : defunct=1 immédiat,
                   backoff long avant re-test (réactivation si retour)
    sinon       -> échec d'API : backoff croissant, defunct après seuil
    """
    now = int(time.time())
    if ok:
        cat.conn.execute("""
            INSERT INTO model_probe_state
                (provider_id, endpoint_id, key_ref, model_id,
                 consecutive_failures, backoff_s, next_probe_at,
                 last_status, last_error, last_probed_at, defunct)
            VALUES (
                (SELECT id FROM catalogue_providers WHERE ref = ?),
                ?, ?, ?, 0, 0, 0, 'ok', '', ?, 0)
            ON CONFLICT(endpoint_id, key_ref, model_id) DO UPDATE SET
                consecutive_failures = 0,
                backoff_s = 0,
                next_probe_at = 0,
                last_status = 'ok',
                last_error = '',
                last_probed_at = excluded.last_probed_at,
                defunct = 0
        """, (provider_ref, endpoint_id, key_ref, model_id, now))
        return

    if absent:
        # Disparu du listing : marqué defunct, re-test après DEFUNCT_REPROBE_S
        cat.conn.execute("""
            INSERT INTO model_probe_state
                (provider_id, endpoint_id, key_ref, model_id,
                 consecutive_failures, backoff_s, next_probe_at,
                 last_status, last_error, last_probed_at, defunct)
            VALUES (
                (SELECT id FROM catalogue_providers WHERE ref = ?),
                ?, ?, ?, 0, ?, ?, 'absent', 'disparu du listing API', ?, 1)
            ON CONFLICT(endpoint_id, key_ref, model_id) DO UPDATE SET
                backoff_s = excluded.backoff_s,
                next_probe_at = excluded.next_probe_at,
                last_status = 'absent',
                last_error = 'disparu du listing API',
                last_probed_at = excluded.last_probed_at,
                defunct = 1
        """, (provider_ref, endpoint_id, key_ref, model_id,
              DEFUNCT_REPROBE_S, now + DEFUNCT_REPROBE_S, now))
        return

    # Échec d'API : compteur + backoff croissant + defunct après seuil
    row = cat.conn.execute("""
        SELECT consecutive_failures FROM model_probe_state
        WHERE endpoint_id = ? AND key_ref = ? AND model_id = ?
    """, (endpoint_id, key_ref, model_id)).fetchone()
    failures = (row["consecutive_failures"] if row else 0) + 1
    backoff = min(BACKOFF_BASE_S * (BACKOFF_FACTOR ** (failures - 1)),
                  BACKOFF_MAX_S)
    defunct = 1 if failures >= DEFUNCT_THRESHOLD else 0
    cat.conn.execute("""
        INSERT INTO model_probe_state
            (provider_id, endpoint_id, key_ref, model_id,
             consecutive_failures, backoff_s, next_probe_at,
             last_status, last_error, last_probed_at, defunct)
        VALUES (
            (SELECT id FROM catalogue_providers WHERE ref = ?),
            ?, ?, ?, ?, ?, ?, 'error', ?, ?, ?)
        ON CONFLICT(endpoint_id, key_ref, model_id) DO UPDATE SET
            consecutive_failures = excluded.consecutive_failures,
            backoff_s = excluded.backoff_s,
            next_probe_at = excluded.next_probe_at,
            last_status = 'error',
            last_error = excluded.last_error,
            last_probed_at = excluded.last_probed_at,
            defunct = excluded.defunct
    """, (provider_ref, endpoint_id, key_ref, model_id, failures,
          backoff, now + backoff, (error or "")[:500], now, defunct))


def _already_probed(cat, endpoint_id: int, key_ref: str, model_id: int) -> bool:
    """True si le modèle est en backoff (next_probe_at dans le futur).

    Un modèle defunct (disparu) est re-testé dès que next_probe_at est
    dépassé : s'il revient dans l'API, on le réactive (defunct=0).
    """
    now = int(time.time())
    row = cat.conn.execute("""
        SELECT next_probe_at FROM model_probe_state
        WHERE endpoint_id = ? AND key_ref = ? AND model_id = ?
    """, (endpoint_id, key_ref, model_id)).fetchone()
    if not row:
        return False
    return (row["next_probe_at"] or 0) > now


def sync_once() -> dict:
    """Un cycle complet de synchro. Retourne un résumé {provider: stats}."""
    from modules.llm_manager.direct_bridge import DirectBridge
    from modules.sql.db import ModelWeaverDB

    cat = _open_cat()
    mw = None
    try:
        try:
            mw = ModelWeaverDB()
        except Exception:
            mw = None
        groups = _key_groups(cat, mw)
        summary = {}
        for provider_ref, endpoint_id, key_ref in groups:
            stats = summary.setdefault(
                provider_ref, {"found": 0, "absent": 0, "skipped": 0,
                               "error": "", "status": "ok"})

            # Modèles déjà déclarés pour (endpoint, key)
            declared = cat.conn.execute("""
                SELECT m.id AS model_id, m.ref AS model_ref
                FROM provider_models_mapping kem
                JOIN catalogue_models m ON m.id = kem.model_id
                WHERE kem.endpoint_id = ? AND kem.key_ref = ?
            """, (endpoint_id, key_ref)).fetchall()

            # Un seul appel API par (provider, key) ; si TOUS les modèles du
            # groupe sont en backoff, on saute l'appel.
            pending = [d for d in declared
                       if not _already_probed(cat, endpoint_id, key_ref,
                                              d["model_id"])]
            if not pending and declared:
                stats["skipped"] += len(declared)
                stats["status"] = "backoff"
                continue

            bridge = DirectBridge(cat=cat)
            try:
                models = bridge.list_available_models(provider_ref)
            except Exception as e:
                stats["error"] = str(e)[:300]
                stats["status"] = "error"
                for d in declared:
                    _upsert_probe_state(cat, endpoint_id, key_ref,
                                        provider_ref, d["model_id"],
                                        ok=False, error=str(e))
                continue

            if not models:
                stats["error"] = "liste vide (API inaccessible ou 0 modèle)"
                stats["status"] = "error"
                for d in declared:
                    _upsert_probe_state(cat, endpoint_id, key_ref,
                                        provider_ref, d["model_id"],
                                        ok=False, error=stats["error"])
                continue

            available = {m["ref"] for m in models}
            for d in declared:
                # La ref catalogue est préfixée (groq/llama-3.3-70b-versatile),
                # la ref API est brute (llama-3.3-70b-versatile).
                present = d["model_ref"] in available
                if not present and "/" in d["model_ref"]:
                    base = d["model_ref"].split("/", 1)[1]
                    present = base in available

                if present:
                    cat.conn.execute("""
                        UPDATE provider_models_mapping
                        SET declared = 1, available = 1, last_checked_at = ?,
                            last_error = NULL
                        WHERE endpoint_id = ? AND key_ref = ? AND model_id = ?
                    """, (int(time.time()), endpoint_id, key_ref, d["model_id"]))
                    _upsert_probe_state(cat, endpoint_id, key_ref,
                                        provider_ref, d["model_id"], ok=True)
                    stats["found"] += 1
                else:
                    # Modèle DISPARU de l'API : on le renseigne.
                    cat.conn.execute("""
                        UPDATE provider_models_mapping
                        SET declared = 0, available = 0, last_checked_at = ?,
                            last_error = 'disparu du listing API'
                        WHERE endpoint_id = ? AND key_ref = ? AND model_id = ?
                    """, (int(time.time()), endpoint_id, key_ref, d["model_id"]))
                    _upsert_probe_state(cat, endpoint_id, key_ref,
                                        provider_ref, d["model_id"],
                                        ok=False, absent=True)
                    stats["absent"] += 1

            # Nouveaux modèles découverts : on les ajoute au catalogue
            existing = {d["model_ref"] for d in declared}
            for m in models:
                ref = m["ref"]
                catalog_ref = f"{provider_ref}/{ref}"
                if catalog_ref in existing or ref in existing:
                    continue
                _insert_new_model(cat, provider_ref, endpoint_id, key_ref,
                                  catalog_ref, m)
                stats["found"] += 1
        return summary
    finally:
        try:
            cat.close()
        except Exception:
            pass
        if mw is not None:
            try:
                mw.close()
            except Exception:
                pass


def _insert_new_model(cat, provider_ref: str, endpoint_id: int,
                      key_ref: str, catalog_ref: str, m: dict) -> None:
    """Ajoute un modèle découvert par l'API au catalogue (si absent).

    ``catalog_ref`` est la ref préfixée ({provider}/{ref API}) qui suit la
    convention du catalogue ; ``m["ref"]`` est la ref brute côté API.
    """
    ref = catalog_ref
    name = m.get("name") or m["ref"]
    try:
        cat.conn.execute("""
            INSERT OR IGNORE INTO catalogue_models (ref, name)
            VALUES (?, ?)
        """, (ref, name))
        cat.conn.execute("""
            INSERT OR IGNORE INTO provider_models
                (provider_id, model_id, provider_model_name, status)
            VALUES (
                (SELECT id FROM catalogue_providers WHERE ref = ?),
                (SELECT id FROM catalogue_models WHERE ref = ?),
                ?, 'active')
        """, (provider_ref, ref, ref))
        cat.conn.execute("""
            INSERT OR IGNORE INTO provider_models_mapping
                (provider_id, endpoint_id, key_ref, model_id,
                 provider_model_name, declared, available, last_checked_at)
            VALUES (
                (SELECT id FROM catalogue_providers WHERE ref = ?),
                ?, ?,
                (SELECT id FROM catalogue_models WHERE ref = ?),
                ?, 1, 1, ?)
        """, (provider_ref, endpoint_id, key_ref, ref, ref, int(time.time())))
        model_id = cat.conn.execute(
            "SELECT id FROM catalogue_models WHERE ref = ?", (ref,)
        ).fetchone()
        if model_id:
            _upsert_probe_state(cat, endpoint_id, key_ref, provider_ref,
                                model_id["id"], ok=True)
    except Exception as e:
        _log(f"  insert {ref} ignoré: {e}")


def main() -> None:
    interval = _interval_seconds()
    _log(f"model_sync demarre (intervalle={interval/3600:.2f}h, "
         f"backoff base={BACKOFF_BASE_S}s, defunct seuil={DEFUNCT_THRESHOLD})")
    try:
        while True:
            try:
                summary = sync_once()
                _log(f"cycle: {summary}")
            except Exception as e:
                _log(f"erreur cycle: {e}")
            time.sleep(interval)
    except KeyboardInterrupt:
        _log("model_sync arrete")


if __name__ == "__main__":
    main()
