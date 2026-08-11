#!/usr/bin/env python3
"""catalogue_local — Domaine du catalogue local (skills/agents/teams/…).

Architecture :
  - LECTURES directes pour tout le monde (connexion mode=ro, aucun write).
  - ÉCRITURES centralisées par le service write_catalogue (token exigé),
    le SEUL writer autorisé.
  - Types de données dynamiques : pour chaque type x (agent, skill, …) on
    crée à chaud x_catalogue, x_tags, x_tag_types, x_limites,
    x_source_and_sharing.
  - Namespaces imbriqués (table namespaces) pour la résolution runtime
    "catalogue.namespace....fn".
  - last_access bufferisé en MÉMOIRE (jamais d'écriture par read, mode=ro) ;
    flush par lots par le writer. last_modify écrit par le writer seul.

Champs obligatoires d'une entrée (socle commun, tous types) :
  id, ref ("namespace/name@version"), name, namespace, version, value,
  description, data_type, status.
"""

# ═══════════════════════════════════════════════════════════════════════════
# SPEC — SYSTÈME D'AUTORISATION (cible à implémenter)
# ═══════════════════════════════════════════════════════════════════════════
#
# Le système d'autorisation converge vers UN SEUL mécanisme : la table
# `privileges` (+ `privilege_conditions`) est l'unique source de vérité pour
# autoriser des PATHS, des COMMANDES et des ACTIONS — fini les whitelists
# statiques, fini les mécanismes parallèles.
#
# ---------------------------------------------------------------------------
# 1. CE QU'UNE AUTORISATION REPRÉSENTE
# ---------------------------------------------------------------------------
# Une ligne `privileges` = "qui, sur quoi, comment, combien de temps".
#   - chemin_ref  : la cible. Interprétée selon `kind` :
#       kind='ref'  → ref canonique du catalogue (utils/bubble_sort@v1), un
#                     namespace (utils) couvre ses descendants.
#       kind='path' → chemin système / symbolique (VFS). Variables $1…,
#                     wildcard * (lecture seule), précision gauche→droite.
#       kind='cmd'  → COMMANDE. La LIGNE COMPLÈTE compte (python3 fichier1.py
#                     ≠ python3 x.py). Une déclaration à 1 token (python3)
#                     couvre toute commande commençant par lui ; une
#                     déclaration complète ne matche que cette ligne exacte
#                     (+ $1/* dans les tokens).
#   - agent_id : -1 = TOUS les agents (défaut global), sinon l'agent ciblé.
#   - team     : -1 = TOUTES les teams (défaut), sinon la team ciblée.
#   - level    : IMPORTANCE, 0..MAX_UINT32 (4294967295). MAX_UINT32 = TOUJOURS
#                (priorité absolue, aucun autre niveau ne le surcharge).
#   - read/write/exec/privileged : MODE UNIX 4 GROUPES, 1 char par niveau :
#        position 0 = humain_with_root, 1 = humain, 2 = agent_with_root,
#        3 = agent. 'r'/'w'/'x'/'p' présent, '-' absent (ex read='-rr-').
#        `privileged` = action nécessitant un privilège root (ask sudo).
#   - deadline  : expiration TEMPORELLE (date ISO) ; NULL/'' = jamais.
#   - conditions: 0..N lignes privilege_conditions (INTERSECTION : toutes
#                 doivent être satisfaites) :
#        nb_times      → valeur = nb max d'usages ; compteur décrémenté à
#                        CHAQUE usage réel (priv/use).
#        until_restart → valide jusqu'au prochain redémarrage (état de session).
#        until_date    → valide jusqu'à une date (valeur = ISO).
#        ref_id        → hérite de la validité de l'autorisation id_auth=valeur
#                        (si elle expire, celle-ci expire aussi).
#
# ---------------------------------------------------------------------------
# 2. RÉSOLUTION D'UN ACCÈS
# ---------------------------------------------------------------------------
# Pour une demande (chemin, agent_id, team, level, op) :
#   1. COLLECTE des lignes qui matchent (kind + identité agent/team).
#   2. FILTRE des lignes invalides : deadline passée, condition non satisfaite.
#   3. PRIORITÉ par level : les lignes de MAX_UINT32 (=TOUJOURS) priment sur
#      tout ; sinon on garde les lignes du PLUS HAUT level.
#   4. EXCLUSION PAR PRÉCISION : si un raffinement plus précis existe dans la
#      même famille et ne matche pas la cible, la règle base est EXCLUE (refus
#      implicite du non-couvert). Ex : 'python3' exclu pour 'python3 x.py' si
#      'python3 fichier1.py' existe.
#   5. INTERSECTION des modes (le plus restrictif gagne : un '-' quelque part
#      → '-' pour ce niveau).
#   6. L'op est accordé si le mode final a le flag au niveau demandé.
#
# ---------------------------------------------------------------------------
# 3. CHECK vs USE (consommation)
# ---------------------------------------------------------------------------
#   - priv/check  : LECTURE (mode=ro), ne consomme RIEN. Vérifie si l'accès
#                   serait accordé. Utilisé pour tester / afficher.
#   - priv/use    : ÉCRITURE (writer), vérifie PUIS consomme (nb_times
#                   décrémente à chaque usage effectif). C'est la route
#                   d'EXÉCUTION réelle (une commande lancée, un fichier écrit).
#
# ---------------------------------------------------------------------------
# 4. DÉCIDEUR BORNÉ PAR SES PROPRES PRIVILÈGES
# ---------------------------------------------------------------------------
# Le décideur (team_leader OU humain) ne peut JAMAIS accorder plus qu'il ne
# possède lui-même :
#   - À l'approbation, on INTERSECTE la demande avec les privilèges du
#     décideur (level max + intersection des modes) : impossible de dépasser.
#   - Le décideur peut RÉDUIRE la portée (réécrire la cible) : à une demande
#     `read /*`, il répond « non pour /*, mais oui pour /e ».
#   - La réponse = l'ENSEMBLE COMPLET des autorisations finales accordées.
#
# 4bis. DÉLÉGATION HIÉRARCHIQUE (qui peut autoriser qui)
# ---------------------------------------------------------------------------
# Matrice (décideur → bénéficiaires autorisés) :
#   humain_with_root → {humain_with_root, humain, agent_with_root, agent}
#   humain           → {humain, agent}
#   agent_with_root  → {agent_with_root, agent}
#   agent            → {agent}
# Règles :
#   - Un humain_with_root peut autoriser un agent_with_root OU un humain sans
#     root (ex. un admin accorde à un pilote).
#   - Un agent_with_root (ex. team_leader pilote_chat avec root) peut autoriser
#     UN AUTRE agent à utiliser SES autorisations — en y mettant des
#     CONDITIONS (deadline, nb_times, lastcall…).
#   - Le bénéficiaire reçoit l'INTERSECTION (decideur ∩ demande), jamais plus
#     que le décideur.
#   - Route : priv/approve (writer PRIVÉ).
#
# ---------------------------------------------------------------------------
# 5. DEMANDES PAR PATH/REF + REGROUPEMENT
# ---------------------------------------------------------------------------
# Les demandes d'autorisation se font par PATH/REF (jamais fichier par
# fichier) et sont SAUVÉES dans la table (auth_requests).
#   - REGROUPEMENT : si on demande read /a/b/c/d puis read /a/b/c/e, la
#     requête regroupe et propose read /a/b/c/* (élargissement intelligent).
#   - La demande est COMPLÈTE : elle liste ce qu'on a déjà, ce qu'on veut, et
#     la proposition de regroupement. Le décideur voit tout et répond en
#     réécrivant la cible + deadline/conditions.
#
# ---------------------------------------------------------------------------
# 6. DEADLINE + RENOUVELLEMENT
# ---------------------------------------------------------------------------
#   - Chaque autorisation a une deadline (date) ou des conditions (nb_times,
#     until_restart, ref_id).
#   - Les autorisations EXPIRÉES sont écartées de la résolution.
#   - À expiration, une DEMANDE DE RENOUVELLEMENT est générée automatiquement
#     (même cible, nouvelle deadline/conditions, même décideur).
#
# ---------------------------------------------------------------------------
# 7. NIVEAUX D'AUTORISATION PAR DÉFAUT
# ---------------------------------------------------------------------------
# Injectés à la création d'un agent/team et à la mise à jour du team_leader :
#   - member      → level 1000 : home + workspace (read/write), pas de root.
#   - team_leader → level 2000 : + exec/privileged sur les paths de SA team.
#   - À la révocation (démission/changement de leader), on RETIRE les règles.
#
# ---------------------------------------------------------------------------
# 8. BRANCHEMENT mini_shell / CLI
# ---------------------------------------------------------------------------
#   - ShellAuth.check_path : un chemin hors VFS est autorisé si un privilège
#     accorde read. member → niveau 'agent' ; leader/owner → 'agent_with_root'.
#   - is_allowed (commandes) : migré sur privileges kind='cmd' (fini la
#     whitelist permissions.yaml).
#   - Wrapper CLI (carnet d'idées #4) : option --sudo activable/désactivable,
#     escalade tracée, connectée à catalogue_path + privileges.
#
# ---------------------------------------------------------------------------
# 9. RÈGLES D'ÉCRITURE
# ---------------------------------------------------------------------------
#   - Seul write_catalogue (token) écrit. Les lecteurs sont en mode=ro.
#   - `*` interdit dans les DÉCLARATIONS de path (écriture) ; autorisé en
#     LECTURE (glob).
#   - path_name ne commence jamais par /$ ou $.
#   - La résolution ne fait JAMAIS d'écriture (last_access bufferisé, check
#     sans consommation).
#
# ---------------------------------------------------------------------------
# 10. BATCHS D'ÉCRITURE
# ---------------------------------------------------------------------------
# Le lock/unlock de chaque écriture bloque les reads (SQLite WAL : 1 writer à
# la fois). Les batchs regroupent N opérations SQL dans UN SEUL lock + UNE
# SEULE transaction.
#   - Soumission : catalogue_local/write/batch {token, ops, max_duration_s}.
#   - Garde-fou nb de lignes : MAX_BATCH_OPS = 100 lignes SQL par batch.
#   - Garde-fou durée : max_duration_s (5s par défaut) — au-delà, le batch est
#     annulé (rollback). Si la durée est estimée courte, le blocage temporaire
#     des lecteurs est acceptable (WAL).
#   - File FIFO : les batchs sont mis en file et traités par un thread dédié
#     (un batch à la fois). Statut via catalogue_local/batch/status.
#   - Le writer PRIVÉ (autorisations) a SES PROPRES écritures (priv/create,
#     priv/use) — les batchs du writer catalogue ne touchent PAS privileges.
#
# ÉTAT D'IMPLÉMENTATION : table + modes 4-groupes + agent_id/team + level
# (MAX_UINT32=TOUJOURS) + deadline + conditions (nb_times, lastcall,
# until_restart, until_date, ref_id) + exclusion par précision + check/use
# séparés + défauts member/team_leader + branchement ShellAuth (chemins) +
# writer PRIVÉ dédié + batchs (2 files, garde-fous) + espace réservé
# auto-test + approve (matrice de délégation + intersection) SONT implémentés.
# RESTE À FAIRE : regroupement des demandes, renouvellement automatique à
# l'expiration, migration is_allowed (commandes) sur privileges, demande par
# path/ref complète, skills d'auth (auth_allow_all / deny_all / transmit_all),
# test FSM_llm (questions-réponses bridge).
# ═══════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from modules.sql.schema import (
    _default_local_catalogue_db, _row_to_dict, _rows_to_list,
    _add_column_if_missing,
)

SCHEMA = Path(__file__).resolve().parent / "local_catalogue_schema.sql"

# Niveau d'importance maximal (priorité absolue = "toujours").
MAX_PRIV_LEVEL = (1 << 32) - 1   # MAX_UINT32 = 4294967295

# Niveaux de DEMANDE (ask) — du moins au plus restrictif.
ASK_LEVELS = ("none", "security_supervisor", "human", "human_root")

# Espace de nommage RÉSERVÉ aux tests de check officiels. Toute entrée/
# namespace/type commençant par ce préfixe vit DANS cet espace : la création
# hors tests y est interdite (les checks y sont rangés), et le nettoyage est
# trivial (suppression par préfixe). Les skills/agents de test intégrés
# portent ce préfixe dans leur namespace/ref.
RESERVED_TEST_PREFIX = "auto-test-check-official"

# Espaces de nommage réservés (outre auto-test-*) — utilisés par les
# fonctionnalités, non supprimables par un cleanup utilisateur.
RESERVED_SYSTEM_PREFIXES = ("auto-", "system/", "catalogue/")

# Colonnes obligatoires du socle commun (toutes les tables {type}_catalogue).
# ref (canonique "namespace/name@version") ≠ ref_file (adresse système du
# fichier quand value est vide) ≠ path (chemin symbolique/configurable).
BASE_COLUMNS = {
    "id": "INTEGER PRIMARY KEY AUTOINCREMENT",
    "ref": "TEXT UNIQUE NOT NULL",
    "name": "TEXT NOT NULL",
    "namespace": "TEXT NOT NULL DEFAULT ''",
    "version": "TEXT NOT NULL DEFAULT 'latest'",
    "value": "TEXT NOT NULL DEFAULT '{}'",
    "ref_file": "TEXT DEFAULT ''",      # adresse système absolue du fichier
    "path": "TEXT DEFAULT ''",          # chemin symbolique (résolu via catalogue_path)
    "description": "TEXT DEFAULT ''",
    "data_type": "TEXT NOT NULL",
    "status": "TEXT NOT NULL DEFAULT 'active'",
    "created_at": "TEXT DEFAULT (datetime('now'))",
    "updated_at": "TEXT DEFAULT (datetime('now'))",
}

# Valeurs de partage autorisées (source_and_sharing.can_be_shared).
SHARING_LEVELS = ("non", "everyone", "enterprise", "friends", "official")

# Sources possibles d'une donnée.
SOURCE_VALUES = ("perso", "distant", "enterprise", "github/depot", "official")


class CatalogueTypeNotFound(KeyError):
    pass


class WriteDenied(PermissionError):
    pass


class LocalCatalogue:
    """Connexion + repo du domaine local_catalogue.

    Deux modes :
      - read_only (défaut) : connexion `mode=ro` — toute écriture lève une
        erreur SQLite (garanti par le moteur).
      - writer : connexion `rwc` — utilisée par write_catalogue (le seul
        writer autorisé). Toute opération d'écriture exige le token.
    """

    def __init__(self, db_path: Optional[Path] = None,
                 mode: str = "ro", write_token: str = ""):
        self.db_path = Path(db_path) if db_path else _default_local_catalogue_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mode = mode
        self._write_token = write_token
        self._lock = threading.Lock()
        uri = f"file:{self.db_path}?mode={'ro' if mode == 'ro' else 'rwc'}"
        self.conn = sqlite3.connect(uri, uri=True, check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        # Buffer last_access en mémoire (les lecteurs en mode=ro ne peuvent
        # pas écrire ; le writer flushe par lots).
        self._access_buffer: Dict[Tuple[str, str], float] = {}
        # File d'attente des batchs d'écriture : "immediate" (create/destruct
        # auth, prioritaire) + "normal" (modify_use, par batchs). Thread dédié.
        self._batch_immediate: List[Dict[str, Any]] = []
        self._batch_normal: List[Dict[str, Any]] = []
        self._batch_done: List[Dict[str, Any]] = []
        self._batch_worker: Optional[threading.Thread] = None
        if mode != "ro":
            self._ensure_schema()

    # ── Batchs d'écriture ──────────────────────────────────
    # Le lock/unlock de chaque écriture bloque les reads (SQLite WAL :
    # 1 writer à la fois). Les batchs regroupent N opérations dans UN SEUL
    # lock + UNE SEULE transaction, avec une durée limite et une file FIFO
    # traitée par un thread dédié. Les lecteurs (mode=ro) ne sont pas
    # bloqués entre les opérations du batch.
    #
    # DEUX FILES :
    #   - "immediate" : create/destruct d'autorisations (urgent, l'agent
    #     attend pour agir) — traité EN PRIORITÉ.
    #   - "normal"    : modify_use (consommation de compteurs nb_times /
    #     cadence lastcall, etc.) — traité PAR BATCHS, avec un petit sleep
    #     (BATCH_YIELD_S) après un gros batch pour laisser passer les writes
    #     immédiats.
    #
    # BATCH_YIELD_S : après un batch "normal" long (ex. 100 lignes / 200 ms),
    # on laisse respirer les writes urgents (create/destruct) avant le suivant.

    BATCH_YIELD_S = 0.05          # répit accordé après un batch normal
    BATCH_YIELD_THRESHOLD_OPS = 20  # à partir de ce nb d'ops, on cède la main

    def submit_batch(self, ops: List[Dict[str, Any]],
                     max_duration_s: float = 5.0,
                     token: str = "",
                     auto_commit: bool = True,
                     queue: str = "normal") -> Dict[str, Any]:
        """Soumet un batch d'écritures (writer uniquement).

        ``ops`` : liste d'opérations atomiques, chacune = dict SQL exécutable :
            {"sql": "...", "params": [...]}   — exécution directe (1 op).
            {"sqls": ["...", ...], "params_list": [[...], ...]} — exécution
            séquentielle dans la même transaction.
        ``queue`` : "immediate" (create/destruct auth, prioritaire) ou
        "normal" (modify_use, par batchs).
        Retourne {status, batch_id}. L'exécution se fait en file (thread
        dédié) ; ``max_duration_s`` = limite de temps totale du batch."""
        self._check_write(token)
        batch_id = f"b{int(time.time() * 1000)}-{len(self._batch_done) + len(self._batch_immediate) + len(self._batch_normal) + 1}"
        item = {"batch_id": batch_id, "ops": ops, "max_duration_s": max_duration_s,
                "auto_commit": auto_commit, "status": "queued", "error": None,
                "queue": queue if queue in ("immediate", "normal") else "normal"}
        (self._batch_immediate if item["queue"] == "immediate"
         else self._batch_normal).append(item)
        if self._batch_worker is None or not self._batch_worker.is_alive():
            self._start_batch_worker()
        return {"status": "ok", "batch_id": batch_id}

    def batch_status(self, batch_id: str) -> Optional[Dict[str, Any]]:
        for item in list(self._batch_immediate) + list(self._batch_normal) \
                + list(self._batch_done):
            if item["batch_id"] == batch_id:
                return {"batch_id": batch_id, "status": item["status"],
                        "error": item.get("error"), "queue": item.get("queue")}
        return None

    def _start_batch_worker(self) -> None:
        self._batch_worker = threading.Thread(target=self._batch_loop,
                                              daemon=True)
        self._batch_worker.start()

    def _batch_loop(self) -> None:
        """Traite les batchs : file IMMÉDIATE en priorité, puis normale."""
        while True:
            item = self._pop_next_batch()
            if item is None:
                return   # files vides → le worker s'arrête (relancé au besoin)
            item["status"] = "running"
            try:
                self._run_batch(item)
            except Exception as e:  # noqa: BLE001
                item["status"] = "error"
                item["error"] = str(e)
            else:
                item["status"] = "done"
                # Répit après un gros batch NORMAL : laisse passer les writes
                # immédiats (create/destruct auth) avant le prochain batch.
                if item["queue"] == "normal" and \
                        self._batch_size(item) >= self.BATCH_YIELD_THRESHOLD_OPS:
                    time.sleep(self.BATCH_YIELD_S)
            self._batch_done.append(item)

    @staticmethod
    def _batch_size(item: Dict[str, Any]) -> int:
        n = 0
        for op in item.get("ops", []):
            if op.get("sql"):
                n += 1
            n += len(op.get("sqls", []) or [])
        return n

    def _pop_next_batch(self) -> Optional[Dict[str, Any]]:
        with self._lock:
            if self._batch_immediate:
                return self._batch_immediate.pop(0)
            if self._batch_normal:
                return self._batch_normal.pop(0)
            return None

    def _run_batch(self, item: Dict[str, Any]) -> None:
        """Exécute un batch dans UNE transaction (un seul lock pris ici)."""
        max_d = float(item["max_duration_s"] or 5.0)
        t0 = time.monotonic()
        with self._lock:
            try:
                self.conn.execute("BEGIN")
                for op in item["ops"]:
                    if time.monotonic() - t0 > max_d:
                        raise TimeoutError(
                            f"batch dépassé la durée limite ({max_d}s)")
                    if op.get("sql"):
                        self.conn.execute(op["sql"], op.get("params") or ())
                    for sql, params in zip(op.get("sqls", []),
                                           op.get("params_list", [])):
                        self.conn.execute(sql, params or ())
                if item.get("auto_commit", True):
                    self.conn.commit()
                else:
                    self.conn.rollback()   # dry-run / annulation volontaire
            except Exception:
                try:
                    self.conn.rollback()
                except Exception:
                    pass
                raise

    # ── Schéma ──────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        self.conn.executescript(SCHEMA.read_text())
        # Migrations idempotentes pour les bases existantes.
        # V0.1 : id_auth (renomme privilege_id) + deadline + table conditions.
        cols = {r[1] for r in self.conn.execute(
            "PRAGMA table_info(privileges)").fetchall()}
        if "privilege_id" in cols and "id_auth" not in cols:
            try:
                self.conn.executescript("""
                ALTER TABLE privileges RENAME TO privileges_old;
                """)
                self.conn.executescript(SCHEMA.read_text())
                self.conn.execute("""
                INSERT OR IGNORE INTO privileges
                    (id_auth, chemin_ref, kind, agent_id, team, level,
                     read, write, exec, privileged, deadline, description,
                     created_at)
                SELECT privilege_id, chemin_ref, kind, agent_id, team, level,
                       read, write, exec, privileged, NULL, description,
                       created_at
                FROM privileges_old
                """)
                self.conn.execute("DROP TABLE privileges_old")
                self.conn.commit()
            except Exception:
                pass
        _add_column_if_missing(self.conn, "privileges", "agent_id",
                               "INTEGER NOT NULL DEFAULT -1")
        _add_column_if_missing(self.conn, "privileges", "team",
                               "INTEGER NOT NULL DEFAULT -1")
        _add_column_if_missing(self.conn, "privileges", "deadline", "TEXT")
        _add_column_if_missing(self.conn, "privileges", "ask",
                               "TEXT NOT NULL DEFAULT 'none'")
        _add_column_if_missing(self.conn, "privilege_conditions", "last_use_at",
                               "TEXT")
        # kind 'cmd' accepté : le CHECK originel le refuse → recréer si besoin.
        try:
            self.conn.execute("SELECT kind FROM privileges LIMIT 1")
        except Exception:
            pass

    def close(self) -> None:
        self.flush_access()
        try:
            self.conn.close()
        except Exception:
            pass

    # ── Autorité writer ─────────────────────────────────────

    def _check_write(self, token: str) -> None:
        """Refuse toute écriture sans le token du writer (défense app)."""
        if self._mode == "ro":
            raise WriteDenied("catalogue_local en lecture seule (mode=ro)")
        if not self._write_token or token != self._write_token:
            raise WriteDenied("écriture refusée : token writer invalide ou absent")

    @staticmethod
    def _is_reserved(ref: str) -> bool:
        """Vrai si la ref/namespace tombe dans un espace réservé (auto-…)."""
        r = ref.strip().lstrip("/")
        return r.startswith(RESERVED_TEST_PREFIX) or \
            any(r.startswith(p) for p in RESERVED_SYSTEM_PREFIXES)

    def _check_reserved(self, ref: str, allow_tests: bool = True) -> None:
        """Autorise l'écriture dans l'espace réservé auto-test uniquement si
        l'appelant se déclare test (allow_tests). Les autres espaces réservés
        (system/, catalogue/) restent fermés."""
        if self._is_reserved(ref):
            if not (allow_tests and ref.startswith(RESERVED_TEST_PREFIX)):
                raise WriteDenied(
                    f"espace réservé interdit en écriture : {ref!r}")

    def _ensure_base_columns(self, table: str) -> None:
        """Migre idempotente : ajoute les colonnes socle manquantes (à chaud)."""
        cols = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for col, decl in BASE_COLUMNS.items():
            if col not in cols:
                self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {decl}")

    # ── Types de données (hot-add) ──────────────────────────

    @staticmethod
    def _tables_for(type_: str) -> Tuple[str, str, str, str, str]:
        return (f"{type_}_catalogue", f"{type_}_tags",
                f"{type_}_tag_types", f"{type_}_limites",
                f"{type_}_source_and_sharing")

    def list_catalogues(self) -> List[Dict[str, Any]]:
        """Registre des types de données existants."""
        try:
            cur = self.conn.execute(
                "SELECT * FROM catalogues ORDER BY type")
            return _rows_to_list(cur.fetchall())
        except sqlite3.OperationalError:
            return []

    def catalogue_exists(self, type_: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM catalogues WHERE type = ?", (type_,)).fetchone()
        return row is not None

    def create_type(self, type_: str, description: str = "",
                    token: str = "") -> Dict[str, Any]:
        """Crée un nouveau type de données à chaud (writer uniquement).

        Crée les 5 tables {type}_* + enregistre le type dans `catalogues`.
        """
        self._check_write(token)
        self._check_reserved(type_)
        if self.catalogue_exists(type_):
            # Table déjà créée : migre les colonnes socle manquantes.
            cat, *_ = self._tables_for(type_)
            try:
                with self._lock:
                    self._ensure_base_columns(cat)
            except Exception:
                pass
            return {"status": "exists", "type": type_}
        if not type_ or not type_.replace("_", "").isalnum():
            raise ValueError(f"type de données invalide: {type_!r}")
        cat, tags, tag_types, limites, sharing = self._tables_for(type_)
        base = ",\n    ".join(f"{c} {d}" for c, d in BASE_COLUMNS.items())
        with self._lock:
            self.conn.executescript(f"""
CREATE TABLE IF NOT EXISTS {cat} (
    {base}
);
CREATE INDEX IF NOT EXISTS idx_{cat}_ns ON {cat}(namespace);
CREATE INDEX IF NOT EXISTS idx_{cat}_name ON {cat}(name);

CREATE TABLE IF NOT EXISTS {tags} (
    tag_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES {cat}(id) ON DELETE CASCADE,
    tag_type    TEXT NOT NULL,
    tag_value   TEXT NOT NULL,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(entry_id, tag_type, tag_value)
);
CREATE INDEX IF NOT EXISTS idx_{tags}_type ON {tags}(tag_type, tag_value);

CREATE TABLE IF NOT EXISTS {tag_types} (
    tag_type    TEXT PRIMARY KEY,
    description TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS {limites} (
    limite_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id    INTEGER NOT NULL REFERENCES {cat}(id) ON DELETE CASCADE,
    limite      TEXT NOT NULL,
    valeur_json TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    UNIQUE(entry_id, limite)
);

CREATE TABLE IF NOT EXISTS {sharing} (
    entry_id        INTEGER PRIMARY KEY REFERENCES {cat}(id) ON DELETE CASCADE,
    source          TEXT NOT NULL DEFAULT 'perso',
    source_url      TEXT,
    is_from_share   INTEGER NOT NULL DEFAULT 0,
    is_it_shared    INTEGER NOT NULL DEFAULT 0,
    can_be_shared   TEXT NOT NULL DEFAULT 'non',
    shared_at       TEXT,
    created_at      TEXT DEFAULT (datetime('now'))
);
""")
            self.conn.execute(
                "INSERT INTO catalogues(type, description) VALUES (?, ?)",
                (type_, description))
            self.conn.commit()
        return {"status": "ok", "type": type_}

    # ── Namespaces ──────────────────────────────────────────

    def create_namespace(self, ns: str, parent: Optional[str] = None,
                         description: str = "", token: str = "") -> Dict[str, Any]:
        """Crée un namespace (imbriqué via parent ou chemin complet)."""
        self._check_write(token)
        self._check_reserved(ns)
        # "utils/sort" → parent "utils" si non précisé
        if parent is None and "/" in ns:
            parent = ns.rsplit("/", 1)[0]
        with self._lock:
            self.conn.execute(
                "INSERT OR IGNORE INTO namespaces(ns, parent, description) "
                "VALUES (?, ?, ?)", (ns, parent, description))
            self.conn.commit()
        return {"status": "ok", "ns": ns, "parent": parent}

    def get_namespace(self, ns: str) -> Optional[Dict[str, Any]]:
        return _row_to_dict(self.conn.execute(
            "SELECT * FROM namespaces WHERE ns = ?", (ns,)).fetchone())

    def list_namespaces(self, parent: Optional[str] = None) -> List[Dict[str, Any]]:
        if parent is None:
            cur = self.conn.execute(
                "SELECT * FROM namespaces ORDER BY ns")
        else:
            cur = self.conn.execute(
                "SELECT * FROM namespaces WHERE parent = ? ORDER BY ns",
                (parent,))
        return _rows_to_list(cur.fetchall())

    def list_namespaces_recursive(self, prefix: str = "") -> List[Dict[str, Any]]:
        """Tous les namespaces dont le chemin commence par `prefix`."""
        if prefix:
            cur = self.conn.execute(
                "SELECT * FROM namespaces WHERE ns = ? OR ns LIKE ? ORDER BY ns",
                (prefix, prefix + "/%"))
        else:
            cur = self.conn.execute("SELECT * FROM namespaces ORDER BY ns")
        return _rows_to_list(cur.fetchall())

    # ── catalogue_path : environnement de paths local ──────

    def create_path(self, path_name: str, address: str,
                    scheme: str = "file", description: str = "",
                    token: str = "") -> Dict[str, Any]:
        """Déclare un path symbolique → adresse système (writer seul)."""
        from modules.sql.catalogue_path import (
            validate_path_name, validate_address,
        )
        self._check_write(token)
        validate_path_name(path_name)
        validate_address(address)
        with self._lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO catalogue_path "
                "(path_name, address, scheme, description) VALUES (?, ?, ?, ?)",
                (path_name, address, scheme, description))
            self.conn.commit()
        return {"status": "ok", "path_name": path_name, "address": address}

    def list_paths(self, scheme: str = "") -> List[Dict[str, Any]]:
        if scheme:
            cur = self.conn.execute(
                "SELECT * FROM catalogue_path WHERE scheme = ? ORDER BY path_name",
                (scheme,))
        else:
            cur = self.conn.execute(
                "SELECT * FROM catalogue_path ORDER BY path_name")
        return _rows_to_list(cur.fetchall())

    def resolve_path(self, target: str) -> Dict[str, Any]:
        """Résout un path symbolique/variable vers son adresse système.

        Précision gauche→droite (catalogue_path.resolve_path). Retourne
        {address, scheme} ou une erreur si non résoluble."""
        from modules.sql.catalogue_path import resolve_path as _resolve
        paths = self.list_paths()
        try:
            address, scheme = _resolve(paths, target)
            return {"status": "ok", "address": address, "scheme": scheme,
                    "input": target}
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}

    # ── privileges : autorisations par chemin ───────────────

    def create_privilege(self, chemin_ref: str, kind: str = "path",
                         level: int = 1, read: str = "----",
                         write: str = "----", exec_: str = "----",
                         privileged: str = "----", description: str = "",
                         agent_id: int = -1, team: int = -1,
                         deadline: str = "", conditions: Optional[list] = None,
                         ask: str = "none",
                         token: str = "") -> Dict[str, Any]:
        """Déclare une règle d'autorisation (writer seul).

        agent_id = -1 → tous les agents ; team = -1 → toutes les teams.
        kind ∈ ref | path | cmd (cmd = autorise l'exécution d'une commande).
        deadline : date ISO d'expiration ('' = jamais).
        conditions : [{type: nb_times|until_restart|until_date|ref_id, valeur}]
                     — une ligne par condition, INTERSECTION.
        ask : niveau de DEMANDE requis pour cette action/commande, SÉPARÉ du
              droit de la faire : none | security_supervisor | human |
              human_root. Ex. une commande peut être autorisée (exec) MAIS
              exiger une confirmation humaine (ask=human) ou humain+root
              (ask=human_root, popup sudo)."""
        from modules.sql.catalogue_privilege import validate_mode
        self._check_write(token)
        if kind not in ("ref", "path", "cmd"):
            raise ValueError(f"kind invalide: {kind!r} (ref|path|cmd)")
        if ask not in ("none", "security_supervisor", "human", "human_root"):
            raise ValueError(f"ask invalide: {ask!r}")
        read = validate_mode(read, "read")
        write = validate_mode(write, "write")
        exec_ = validate_mode(exec_, "exec")
        privileged = validate_mode(privileged, "privileged")
        if not (0 <= int(level) <= MAX_PRIV_LEVEL):
            raise ValueError(f"level hors bornes (0..{MAX_PRIV_LEVEL}): {level!r}")
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO privileges "
                "(chemin_ref, kind, agent_id, team, level, read, write, exec, "
                "privileged, ask, deadline, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chemin_ref, kind, int(agent_id), int(team), int(level),
                 read, write, exec_, privileged, ask, deadline or None, description))
            id_auth = cur.lastrowid
            for c in (conditions or []):
                self.conn.execute(
                    "INSERT INTO privilege_conditions(id_auth, condition_type, valeur, compteur) "
                    "VALUES (?, ?, ?, ?)",
                    (id_auth, c.get("type", ""),
                     json.dumps(c.get("valeur")) if not isinstance(c.get("valeur"), str)
                     else c.get("valeur"),
                     c.get("compteur", c.get("valeur") if c.get("type") == "nb_times" else None)))
            self.conn.commit()
        return {"status": "ok", "id_auth": id_auth,
                "chemin_ref": chemin_ref, "kind": kind, "ask": ask}

    def list_privileges(self, kind: str = "") -> List[Dict[str, Any]]:
        if kind:
            cur = self.conn.execute(
                "SELECT * FROM privileges WHERE kind = ? "
                "ORDER BY level DESC, chemin_ref", (kind,))
        else:
            cur = self.conn.execute(
                "SELECT * FROM privileges ORDER BY level DESC, chemin_ref")
        return _rows_to_list(cur.fetchall())

    def _match_privilege_rows(self, chemin: str, kind: str = "",
                              agent_id: int = -1, team: int = -1) -> List[Dict[str, Any]]:
        """Lignes privileges dont le chemin intersecte `chemin` demandé.

        - Filtre identité : agent_id == agent_id demandé OU -1 (tous) ;
          team == team demandée OU -1 (toutes).
        - kind='ref' : chemin_ref == chemin (ou préfixe de namespace, ex.
          'utils' couvre 'utils/bubble_sort@v1').
        - kind='path' : le chemin demandé est DANS le path déclaré (les deux
          sens) — on matche les déclarations dont les segments littéraux
          correspondent, avec substitution des variables $1…
        - kind='cmd' : chemin_ref == base de la commande (ex. 'git')."""
        sql = "SELECT * FROM privileges"
        params: List[Any] = []
        conds: List[str] = []
        if kind:
            conds.append("kind = ?"); params.append(kind)
        if agent_id != -1:
            conds.append("(agent_id = ? OR agent_id = -1)")
            params.append(int(agent_id))
        if team != -1:
            conds.append("(team = ? OR team = -1)")
            params.append(int(team))
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        rows = _rows_to_list(self.conn.execute(sql, params).fetchall())
        # toutes les règles de la famille (même kind + identité), pour
        # l'exclusion par précision.
        family = _rows_to_list(self.conn.execute(sql, params).fetchall())
        out = []
        for r in rows:
            rk = r.get("kind")
            ref = r.get("chemin_ref", "")
            if rk == "ref":
                if chemin == ref or ref and (chemin + "/").startswith(ref + "/"):
                    out.append(r)
            elif rk == "cmd":
                if self._cmd_contains(ref, chemin):
                    out.append(r)
            else:  # path
                if self._path_contains(ref, chemin):
                    out.append(r)
        # EXCLUSION PAR PRÉCISION : si un raffinement PLUS PRÉCIS existe dans
        # la même famille et ne matche pas le chemin demandé, la règle base
        # (moins précise) est EXCLUE → refus par défaut pour le non-couvert.
        # Ex : 'python3' exclu pour 'python3 x.py' si 'python3 fichier1.py'
        # existe (refus implicite), alors que 'python3 fichier1.py' est accordé.
        return self._apply_precision_exclusion(out, chemin, family)

    def _apply_precision_exclusion(self, rows: List[Dict[str, Any]],
                                   chemin: str,
                                   family: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Exclut les règles BASE quand un raffinement plus précis couvre déjà
        la famille (refus implicite du non-couvert).

        Une règle R1 est « plus précise » que R0 si R0 est un PREFIXE de R1
        (pour cmd : tokens ; pour path : segments). Si R0 matche `chemin`
        mais qu'il existe un raffinement R1 de R0 (même famille) qui ne
        matche PAS `chemin`, alors R0 est exclue."""
        kept = []
        for r in rows:
            ref = r.get("chemin_ref", "")
            rk = r.get("kind", "")
            excluded = False
            for r2 in family:
                if r2.get("id_auth") == r.get("id_auth"):
                    continue
                r2_ref = r2.get("chemin_ref", "")
                if self._is_prefix(ref, r2_ref, rk) and \
                        not self._matches(r2, chemin):
                    excluded = True
                    break
            if not excluded:
                kept.append(r)
        return kept

    @staticmethod
    def _is_prefix(a: str, b: str, kind: str) -> bool:
        """Vrai si a est un préfixe strict de b (même famille)."""
        if kind == "cmd":
            as_, bs = a.strip().split(), b.strip().split()
        else:
            as_, bs = a.strip("/").split("/"), b.strip("/").split("/")
        if len(as_) >= len(bs):
            return False
        return as_ == bs[: len(as_)]

    def _matches(self, row: Dict[str, Any], chemin: str) -> bool:
        """Vrai si la règle matche le chemin demandé."""
        ref = row.get("chemin_ref", "")
        rk = row.get("kind", "")
        if rk == "ref":
            return chemin == ref or ref and (chemin + "/").startswith(ref + "/")
        if rk == "cmd":
            return self._cmd_contains(ref, chemin)
        return self._path_contains(ref, chemin)

    def _path_contains(self, declared: str, target: str) -> bool:
        """Vrai si le path déclaré contient le chemin demandé.

        Matche segment par segment : littéral == littéral, variable $1 absorbe
        un segment, `*` (déclaré) absorbe tout suffixe. Les deux sens :
        target est sous declared."""
        if not declared or not target:
            return False
        dsegs = declared.strip("/").split("/")
        tsegs = target.strip("/").split("/")
        if len(dsegs) > len(tsegs):
            return False
        for i, d in enumerate(dsegs):
            if d == "*":
                return True   # wildcard absorbe le reste
            if d.startswith("$"):
                continue      # variable absorbe le segment
            if d != tsegs[i]:
                return False
        return True

    def _cmd_contains(self, declared: str, target: str) -> bool:
        """Vrai si la déclaration de commande couvre la commande demandée.

        La commande est tokenisée (python3 fichier1.py). Une déclaration à un
        seul token (`python3`) matche toute commande qui commence par lui ;
        une déclaration complète (`python3 fichier1.py`) ne matche que la
        ligne exacte (ou ses variantes `$1`/`*`).

        Exemples :
          declared="python3"          target="python3 x.py"     → True
          declared="python3 fichier1.py" target="python3 x.py"  → False
          declared="python3 fichier1.py" target="python3 fichier1.py" → True
          declared="python3 $1"       target="python3 x.py"     → True
          declared="python3 *"        target="python3 x.py y"   → True"""
        if not declared or not target:
            return False
        dsegs = str(declared).strip().split()
        tsegs = str(target).strip().split()
        if not dsegs or not tsegs:
            return False
        # déclaration à un seul token → couvre toute commande qui commence par lui
        if len(dsegs) == 1 and dsegs[0] not in ("*",) and not dsegs[0].startswith("$"):
            return tsegs[0] == dsegs[0]
        if len(dsegs) > len(tsegs):
            return False
        for i, d in enumerate(dsegs):
            if d == "*":
                return True   # wildcard absorbe le reste
            if d.startswith("$"):
                continue      # variable absorbe le token
            if d != tsegs[i]:
                return False
        return True

    def _conditions_for(self, id_auth: int) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM privilege_conditions WHERE id_auth = ? "
            "ORDER BY condition_id", (int(id_auth),)).fetchall())

    def _ref_lookup(self, id_auth: int) -> Optional[Dict[str, Any]]:
        """Cherche une autorisation par id_auth (pour ref_id)."""
        row = self.conn.execute(
            "SELECT * FROM privileges WHERE id_auth = ?", (int(id_auth),)).fetchone()
        if row is None:
            return None
        r = dict(row)
        r["_conditions"] = self._conditions_for(r["id_auth"])
        return r

    def resolve_privilege(self, chemin: str, kind: str = "",
                          agent_id: int = -1, team: int = -1,
                          consume: bool = False) -> Dict[str, Any]:
        """Résout les privilèges d'un chemin/ref/commande.

        Ne garde que les lignes du PLUS HAUT level qui matchent (agent_id/team
        ciblés ou -1), puis INTERSECTE leurs modes (le plus restrictif gagne).
        Une ligne à level == MAX_UINT32 = TOUJOURS. Les lignes EXPIRÉES
        (deadline) ou à conditions non satisfaites sont écartées. La
        CONSOMMATION (nb_times) ne se fait qu'à l'usage réel via use_privilege
        (writer) — jamais dans un simple check (mode=ro)."""
        from modules.sql.catalogue_privilege import (
            select_by_level, intersect_modes, parse_privileges,
            is_deadline_passed, conditions_satisfied, consume_conditions,
        )
        rows = self._match_privilege_rows(chemin, kind=kind,
                                          agent_id=agent_id, team=team)
        # écarte les lignes expirées (deadline) ; charge leurs conditions
        valid = []
        for r in rows:
            if is_deadline_passed(r):
                continue
            r["_conditions"] = self._conditions_for(r["id_auth"])
            if conditions_satisfied(r["_conditions"], self._ref_lookup):
                valid.append(r)
        if not valid:
            return {"status": "ok", "read": "----", "write": "----",
                    "exec": "----", "privileged": "----", "matched": 0}
        # "TOUJOURS" (MAX_UINT32) prime : on ne garde que ces lignes-là.
        always = [r for r in valid if (r.get("level", 1) or 1) >= MAX_PRIV_LEVEL]
        rows = always if always else select_by_level(valid)
        modes = {col: [] for col in ("read", "write", "exec", "privileged")}
        consumed = False
        ask_idx = 0   # le plus restrictif des ask des lignes retenues
        for r in rows:
            for col, mode in parse_privileges(r).items():
                modes[col].append(mode)
            try:
                a = ASK_LEVELS.index(r.get("ask") or "none")
                if a > ask_idx:
                    ask_idx = a
            except (ValueError, TypeError):
                pass
            if consume and any(c.get("condition_type") == "nb_times"
                               for c in r.get("_conditions", [])):
                self._consume_nb_times(r["id_auth"])
                consumed = True
        return {"status": "ok",
                "read": intersect_modes(modes["read"], "read"),
                "write": intersect_modes(modes["write"], "write"),
                "exec": intersect_modes(modes["exec"], "exec"),
                "privileged": intersect_modes(modes["privileged"], "privileged"),
                "matched": len(rows),
                "level": rows[0].get("level", 1),
                "ask": ASK_LEVELS[ask_idx],
                "always": bool(always),
                "consumed": consumed}

    def _consume_conditions(self, id_auth: int) -> None:
        """modify_use : applique l'effet des conditions d'usage d'une auth.

        - nb_times : décrémente le compteur.
        - lastcall : met à jour last_use_at (cadence).
        C'est la seule fonction qui écrit les compteurs — passée par le
        writer (modify_use), jamais par un check (mode=ro)."""
        try:
            self.conn.execute(
                "UPDATE privilege_conditions SET compteur = compteur - 1 "
                "WHERE id_auth = ? AND condition_type = 'nb_times' "
                "AND compteur IS NOT NULL AND compteur > 0",
                (int(id_auth),))
            self.conn.execute(
                "UPDATE privilege_conditions SET last_use_at = datetime('now') "
                "WHERE id_auth = ? AND condition_type = 'lastcall'",
                (int(id_auth),))
            self.conn.commit()
        except Exception:
            pass

    def use_privilege(self, chemin: str, level: str = "agent",
                      op: str = "read", kind: str = "",
                      agent_id: int = -1, team: int = -1,
                      token: str = "") -> Dict[str, Any]:
        """(writer PRIVÉ) Vérifie ET consomme une autorisation à l'usage réel.

        Flux (modify_use) :
          1. CHECK : résolution (sans consommation) du mode pour (chemin, level,
             op, agent_id, team).
          2. Si l'op est accordé ET l'autorisation porte une CONDITION D'USAGE
             (nb_times OU lastcall) → demander au writer de l'appliquer
             (_consume_conditions : décrément + last_use_at).
          3. Si une limite d'usage est épuisée (nb_times à 0, ou lastcall non
             satisfait) → signaler `renewal_request` (même cible, décideur).

        Retourne {allowed, consumed, remaining, renewal_request}.
        """
        self._check_write(token)
        from modules.sql.catalogue_privilege import LEVEL_INDEX
        if level not in LEVEL_INDEX:
            raise ValueError(f"level invalide: {level!r}")
        if op not in ("read", "write", "exec", "privileged"):
            raise ValueError(f"op invalide: {op!r}")
        res = self.resolve_privilege(chemin, kind=kind, agent_id=agent_id,
                                     team=team, consume=False)
        mode = res.get(op, "----")
        idx = LEVEL_INDEX[level]
        flag = {"read": "r", "write": "w", "exec": "x",
                "privileged": "p"}[op]
        allowed = len(mode) == 4 and mode[idx] == flag

        consumed = 0
        remaining = None
        renewal_request = None
        # autorisations à CONDITION D'USAGE (nb_times / lastcall) couvrant la cible
        limited = []
        for r in self._match_privilege_rows(chemin, kind=kind,
                                            agent_id=agent_id, team=team):
            conds = self._conditions_for(r["id_auth"])
            if any(c.get("condition_type") in ("nb_times", "lastcall")
                   for c in conds):
                limited.append((r, conds))

        if allowed and limited:
            for r, conds in limited:
                before = None
                for c in conds:
                    if c.get("condition_type") == "nb_times":
                        before = c.get("compteur")
                if before is None or int(before) > 0:
                    self._consume_conditions(r["id_auth"])
                    consumed += 1
                    # after : relire l'état post-consommation
                    after = None
                    for c in self._conditions_for(r["id_auth"]):
                        if c.get("condition_type") == "nb_times":
                            after = c.get("compteur")
                    remaining = int(after) if after is not None else None
                    if after is not None and int(after) <= 0:
                        renewal_request = {
                            "reason": "limite d'usage épuisée",
                            "chemin_ref": r.get("chemin_ref"),
                            "kind": r.get("kind"),
                            "op": op, "level": level,
                            "agent_id": r.get("agent_id"),
                            "team": r.get("team"),
                            "expired_id_auth": r["id_auth"],
                        }
        elif not allowed and limited:
            # limite d'usage déjà épuisée (nb_times=0 / lastcall cadence) →
            # refus + renouvellement
            r, conds = limited[0]
            renewal_request = {
                "reason": "limite d'usage épuisée (check)",
                "chemin_ref": r.get("chemin_ref"),
                "kind": r.get("kind"), "op": op, "level": level,
                "agent_id": r.get("agent_id"), "team": r.get("team"),
                "expired_id_auth": r["id_auth"],
            }
        return {"status": "ok", "allowed": allowed,
                "op": op, "level": level, "consumed": consumed,
                "remaining": remaining, "renewal_request": renewal_request,
                "ask": res.get("ask", "none"),
                "ask_pending": bool(allowed) and res.get("ask", "none") not in (
                    "none",)}

    def approve_privilege(self, decider_agent_id: int, decider_level: str,
                          beneficiary_agent_id: int, beneficiary_level: str,
                          chemin_ref: str, kind: str = "path",
                          read: str = "", write: str = "", exec_: str = "",
                          privileged: str = "", deadline: str = "",
                          conditions: Optional[list] = None,
                          team: int = -1, token: str = "") -> Dict[str, Any]:
        """(writer PRIVÉ) Un DÉCIDEUR approuve une autorisation pour un tiers.

        Règles (délégation hiérarchique stricte) :
          1. MATRICE : le décideur ne peut déléguer qu'à des niveaux
             autorisés (humain_with_root→{tous}, humain→{humain,agent},
             agent_with_root→{agent_with_root,agent}, agent→{agent}).
          2. BORNAGE : le bénéficiaire reçoit l'INTERSECTION (level max +
             modes) des privilèges du DÉCIDEUR et des modes demandés — jamais
             plus que ce que le décideur possède lui-même.
          3. CONDITIONS : le décideur peut restreindre (deadline, nb_times,
             lastcall…) — ex. un team_leader root (pilote_chat) autorise un
             agent à utiliser SES autorisations pour 1h / 50 usages.

        Retourne {status, id_auth} de la nouvelle autorisation (scoped
        agent_id=bénéficiaire) ou une erreur (délégation refusée / bornage)."""
        self._check_write(token)
        from modules.sql.catalogue_privilege import (
            can_delegate, intersect_modes, validate_mode, COLUMN_FLAG,
        )
        if not can_delegate(decider_level, beneficiary_level):
            return {"status": "error",
                    "error": f"délégation refusée : {decider_level} ne peut pas "
                             f"autoriser {beneficiary_level}"}

        # 1. Privilèges ACTUELS du décideur sur la cible (level max + intersection)
        dec = self.resolve_privilege(chemin_ref, kind=kind,
                                     agent_id=decider_agent_id, team=team)
        # 2. Intersection : décideur ∩ demande (modes demandés, sinon ceux du
        #    décideur) — le bénéficiaire ne peut jamais recevoir plus.
        caps = {}
        for col, flag in COLUMN_FLAG.items():
            demand = {"read": read, "write": write, "exec": exec_,
                      "privileged": privileged}.get(col, "")
            owned = dec.get(col, "----")
            caps[col] = intersect_modes([validate_mode(demand or owned, col),
                                         owned], col)
        # borne supérieure : on ne garde que le level du décideur (pas plus haut)
        level = dec.get("level", 1)

        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO privileges "
                "(chemin_ref, kind, agent_id, team, level, read, write, exec, "
                "privileged, deadline, description) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (chemin_ref, kind, int(beneficiary_agent_id), int(team),
                 int(level), caps["read"], caps["write"], caps["exec"],
                 caps["privileged"], deadline or None,
                 f"délégué par agent {decider_agent_id} ({decider_level})"))
            id_auth = cur.lastrowid
            for c in (conditions or []):
                self.conn.execute(
                    "INSERT INTO privilege_conditions(id_auth, condition_type, valeur, compteur) "
                    "VALUES (?, ?, ?, ?)",
                    (id_auth, c.get("type", ""),
                     json.dumps(c.get("valeur")) if not isinstance(c.get("valeur"), str)
                     else c.get("valeur"),
                     c.get("compteur", c.get("valeur") if c.get("type") == "nb_times" else None)))
            self.conn.commit()
        return {"status": "ok", "id_auth": id_auth, "level": level,
                "read": caps["read"], "write": caps["write"],
                "exec": caps["exec"], "privileged": caps["privileged"]}

    def check_privilege(self, chemin: str, level: str = "agent",
                        op: str = "read", kind: str = "",
                        agent_id: int = -1, team: int = -1) -> bool:
        """Vrai si `level` a le privilège `op` sur `chemin`.

        level ∈ humain_with_root | humain | agent_with_root | agent.
        op ∈ read | write | exec | privileged."""
        from modules.sql.catalogue_privilege import LEVEL_INDEX
        if level not in LEVEL_INDEX:
            raise ValueError(f"level invalide: {level!r}")
        if op not in ("read", "write", "exec", "privileged"):
            raise ValueError(f"op invalide: {op!r}")
        res = self.resolve_privilege(chemin, kind=kind,
                                     agent_id=agent_id, team=team)
        mode = res.get(op, "----")
        idx = LEVEL_INDEX[level]
        flag = {"read": "r", "write": "w", "exec": "x",
                "privileged": "p"}[op]
        return len(mode) == 4 and mode[idx] == flag

    # ── Entrées (lecture directe) ───────────────────────────

    def _cat_table(self, type_: str) -> str:
        if not self.catalogue_exists(type_):
            raise CatalogueTypeNotFound(f"type '{type_}' introuvable")
        return f"{type_}_catalogue"

    def _entry(self, type_: str, ref: str) -> Optional[Dict[str, Any]]:
        """Lecture d'une entrée par ref canonique (namespace/name@version).

        Résolution : ref exacte → ref sans version (latest) → recherche par
        name dans le namespace. Trace last_access (buffer mémoire)."""
        table = self._cat_table(type_)
        row = self.conn.execute(
            f"SELECT * FROM {table} WHERE ref = ?", (ref,)).fetchone()
        if row is None and "@" not in ref:
            row = self.conn.execute(
                f"SELECT * FROM {table} WHERE name = ? ORDER BY version DESC "
                "LIMIT 1", (ref,)).fetchone()
        if row is not None:
            self._touch_access(type_, row["ref"])
        return _row_to_dict(row)

    def get(self, type_: str, ref: str, resolve_value: bool = True) -> Dict[str, Any]:
        """Lecture d'une entrée. Si `value` est vide et `ref` (adresse système)
        ou `path` (symbolique) présent, charge le contenu du fichier
        (résolution au get)."""
        entry = self._entry(type_, ref)
        if entry is None:
            raise CatalogueTypeNotFound(
                f"{type_}/{ref} introuvable dans le catalogue local")
        if resolve_value:
            self._resolve_value(entry)
        return entry

    def _resolve_value(self, entry: Dict[str, Any]) -> None:
        """Remplit `value` depuis le fichier pointé par ref/path (si vide).

        - ref_file : ADRESSE SYSTÈME absolue (utilisée telle quelle).
        - path     : chemin SYMBOLIQUE → résolu via catalogue_path (précision
          gauche→droite, variables $1…)."""
        if (entry.get("value") or "").strip():
            return
        try:
            from modules.sql.catalogue_path import resolve_path as _rp
            if entry.get("ref_file"):
                address = entry["ref_file"]
            elif entry.get("path"):
                paths = self.list_paths()
                address, _scheme = _rp(paths, entry["path"])
            else:
                return
            entry["value"] = self._read_file_value(address)
            entry["_value_source"] = address
        except Exception:  # noqa: BLE001 — best-effort, value reste vide
            pass

    @staticmethod
    def _read_file_value(address: str) -> str:
        """Lit le contenu d'un fichier local (adresse système)."""
        p = Path(address)
        if not p.is_file():
            return ""
        try:
            return p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            try:
                return p.read_bytes().decode("latin-1")
            except Exception:
                return ""

    def _touch_access(self, type_: str, ref: str) -> None:
        """Bufferise le last_access en mémoire (flushé par le writer)."""
        self._access_buffer[(type_, ref)] = time.time()

    def _bump_modify(self, type_: str, ref: str) -> None:
        """(writer) Met à jour last_modify_data (cœur de l'invalidation des
        flux d'infos : si modify_date d'une data < last_modify → périmée)."""
        if self._mode == "ro":
            return
        try:
            self.conn.execute(
                "INSERT INTO last_modify_data(data_type, ref, last_modify_at, modify_count) "
                "VALUES (?, ?, datetime('now'), 1) "
                "ON CONFLICT(data_type, ref) DO UPDATE SET "
                "last_modify_at = datetime('now'), modify_count = modify_count + 1",
                (type_, ref))
        except Exception:
            pass

    def flush_access(self) -> int:
        """(writer) Écrit le buffer last_access en BDD par lots.

        JAMAIS appelé par un lecteur (mode=ro l'empêcherait). Appelé par
        write_catalogue périodiquement."""
        if not self._access_buffer or self._mode == "ro":
            self._access_buffer.clear()
            return 0
        rows = list(self._access_buffer.items())
        self._access_buffer.clear()
        now = "datetime('now')"
        for (type_, ref), _ts in rows:
            try:
                self.conn.execute(
                    f"INSERT INTO last_access_data(data_type, ref, last_access_at, access_count) "
                    f"VALUES (?, ?, {now}, 1) "
                    f"ON CONFLICT(data_type, ref) DO UPDATE SET "
                    f"last_access_at = {now}, access_count = access_count + 1",
                    (type_, ref))
            except Exception:
                pass
        try:
            self.conn.commit()
        except Exception:
            pass
        return len(rows)

    def list(self, type_: str, namespace: str = "",
             page: int = 1, page_size: int = 100,
             sort: str = "name", order: str = "asc",
             status: str = "") -> Dict[str, Any]:
        table = self._cat_table(type_)
        where, params = [], []
        if namespace:
            where.append("namespace = ?"); params.append(namespace)
        if status:
            where.append("status = ?"); params.append(status)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        col = sort if sort in BASE_COLUMNS else "name"
        direction = "ASC" if order.lower() == "asc" else "DESC"
        total = self.conn.execute(
            f"SELECT COUNT(*) AS n FROM {table}{clause}", params).fetchone()["n"]
        offset = max(0, (int(page) - 1) * int(page_size))
        cur = self.conn.execute(
            f"SELECT * FROM {table}{clause} ORDER BY {col} {direction} "
            f"LIMIT ? OFFSET ?", params + [int(page_size), offset])
        return {"items": _rows_to_list(cur.fetchall()),
                "total": total, "page": int(page), "page_size": int(page_size)}

    def list_recursive(self, type_: str, namespace: str = "",
                       status: str = "") -> List[Dict[str, Any]]:
        """Toutes les entrées d'un type, namespace racine ou un sous-arbre."""
        table = self._cat_table(type_)
        if namespace:
            prefix = namespace.rstrip("/")
            cur = self.conn.execute(
                f"SELECT * FROM {table} WHERE namespace = ? OR namespace LIKE ? "
                f"{'AND status = ?' if status else ''} ORDER BY namespace, name",
                ([prefix, prefix + "/%"] + ([status] if status else [])))
        else:
            cur = self.conn.execute(
                f"SELECT * FROM {table} "
                f"{'WHERE status = ?' if status else ''} ORDER BY namespace, name",
                ([status] if status else []))
        return _rows_to_list(cur.fetchall())

    def search(self, type_: str, q: str = "",
               tag_type: str = "", tag_value: str = "",
               limit: int = 50) -> List[Dict[str, Any]]:
        table = self._cat_table(type_)
        sql = f"SELECT * FROM {table}"
        params: List[Any] = []
        conds: List[str] = []
        if q:
            like = f"%{q}%"
            conds.append("(name LIKE ? OR description LIKE ? OR ref LIKE ?)")
            params += [like, like, like]
        if conds:
            sql += " WHERE " + " AND ".join(conds)
        sql += f" ORDER BY name LIMIT {int(limit)}"
        items = _rows_to_list(self.conn.execute(sql, params).fetchall())
        if tag_type:
            # filtre par tag : inner join sur {type}_tags
            tags = f"{type_}_tags"
            out = []
            for it in items:
                row = self.conn.execute(
                    f"SELECT 1 FROM {tags} WHERE entry_id = ? AND tag_type = ? "
                    f"AND tag_value LIKE ?",
                    (it["id"], tag_type, f"%{tag_value or ''}%")).fetchone()
                if row:
                    out.append(it)
            items = out
        return items

    # ── Tags (lecture) ──────────────────────────────────────

    def list_tag_types(self, type_: str) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            f"SELECT * FROM {type_}_tag_types ORDER BY tag_type").fetchall())

    def list_tags(self, type_: str, ref: str) -> List[Dict[str, Any]]:
        entry = self._entry(type_, ref)
        if entry is None:
            return []
        return _rows_to_list(self.conn.execute(
            f"SELECT tag_type, tag_value, created_at FROM {type_}_tags "
            "WHERE entry_id = ? ORDER BY tag_type, tag_value",
            (entry["id"],)).fetchall())

    def tags_by_value(self, type_: str, tag_type: str,
                      tag_value: str) -> List[Dict[str, Any]]:
        """Entrées portant un tag (recherche par tag)."""
        table = f"{type_}_tags"
        cur = self.conn.execute(
            f"SELECT e.* FROM {table} t JOIN {type_}_catalogue e ON e.id = t.entry_id "
            "WHERE t.tag_type = ? AND t.tag_value = ? ORDER BY e.name",
            (tag_type, tag_value))
        return _rows_to_list(cur.fetchall())

    # ── Limites (lecture) ───────────────────────────────────

    def list_limites(self, type_: str, ref: str) -> List[Dict[str, Any]]:
        entry = self._entry(type_, ref)
        if entry is None:
            return []
        return _rows_to_list(self.conn.execute(
            f"SELECT limite, valeur_json FROM {type_}_limites "
            "WHERE entry_id = ? ORDER BY limite", (entry["id"],)).fetchall())

    # ── Source & sharing (lecture) ──────────────────────────

    def get_sharing(self, type_: str, ref: str) -> Optional[Dict[str, Any]]:
        entry = self._entry(type_, ref)
        if entry is None:
            return None
        return _row_to_dict(self.conn.execute(
            f"SELECT * FROM {type_}_source_and_sharing WHERE entry_id = ?",
            (entry["id"],)).fetchone())

    def resolve_can_be_shared(self, type_: str, ref: str) -> str:
        """Résout can_be_shared en cascade : entrée → shared_default.

        Priorité : source_and_sharing.can_be_shared explicite, sinon
        shared_default(data_type, '*', '*')."""
        entry = self._entry(type_, ref)
        if entry is None:
            return "non"
        s = self.conn.execute(
            f"SELECT can_be_shared FROM {type_}_source_and_sharing "
            "WHERE entry_id = ? AND can_be_shared != 'non'",
            (entry["id"],)).fetchone()
        if s:
            return s["can_be_shared"]
        d = self.conn.execute(
            "SELECT can_be_shared FROM shared_default "
            "WHERE data_type = ? AND tag_type = '*' AND tag_value = '*'",
            (type_,)).fetchone()
        return (d["can_be_shared"] if d else "non")

    def list_shared_default(self) -> List[Dict[str, Any]]:
        return _rows_to_list(self.conn.execute(
            "SELECT * FROM shared_default ORDER BY data_type").fetchall())

    # ── Cleanup par préfixe (espace réservé) ────────────────

    def cleanup_prefix(self, prefix: str, token: str = "") -> Dict[str, Any]:
        """Supprime toutes les entrées/namespaces commençant par `prefix`.

        SÉCURITÉ : ne fonctionne que sur l'espace réservé auto-test
        (cleanup trivial des checks officiels). Refuse toute autre cible."""
        self._check_write(token)
        if not prefix.startswith(RESERVED_TEST_PREFIX):
            raise WriteDenied(
                f"cleanup interdit : hors espace réservé auto-test ({prefix!r})")
        like = prefix.rstrip("/") + "/%"
        removed = {"types": 0, "entries": 0, "namespaces": 0}
        try:
            # entrées de chaque type existant
            for c in self.list_catalogues():
                type_ = c["type"]
                table = f"{type_}_catalogue"
                cur = self.conn.execute(
                    f"DELETE FROM {table} WHERE ref LIKE ? OR namespace LIKE ?",
                    (like, like))
                removed["entries"] += cur.rowcount
            removed["namespaces"] = self.conn.execute(
                "DELETE FROM namespaces WHERE ns = ? OR ns LIKE ?",
                (prefix, like)).rowcount
            self.conn.commit()
        except Exception as e:  # noqa: BLE001
            return {"status": "error", "error": str(e)}
        return {"status": "ok", "prefix": prefix, "removed": removed}
