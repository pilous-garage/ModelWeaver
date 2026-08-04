#!/usr/bin/env python3
"""ModelWeaver — Migrations SQL.

Toute la logique d'évolution du schéma, extraite depuis db.py.
Les classes DB conservent leur connexion existante ; ce module applique
uniquement les DDL/idempotent migrations sur la connexion fournie.

Comportement conservé :
- SQLite locks : autocommit explicite sur AgentsDB (`isolation_level=None`),
  commits/rollbacks manuels ailleurs.
- Les migrations sont idempotentes (IF NOT EXISTS / ALTER TABLE ... catch).
"""

import json
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional


# ──────────────────────────────────────────────
#  Helpers partagés (extraits de db.py)
# ──────────────────────────────────────────────

_DEFAULT_CLASS_MAP = {
    "opencode": "agent",
    "litellm": "router",
    "keyring": "context",
    "ollama": "engine",
    # binaires système détectés
    "python3": "language",
    "git": "dev-tool",
    "curl": "dev-tool",
    "open-webui": "chat-llm",
    "gitingest": "context",
    "requests": "dev-tool",
    "psutil": "system",
    "cryptography": "dev-tool",
}


def _ensure_classes_outils_table(conn) -> None:
    """Crée classes_outils + seed si la table n'existe pas encore.

    Idempotent : appelé par _ensure_schema() du catalogue et du local.
    """
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='classes_outils'"
    ).fetchone()
    if exists:
        return
    conn.execute("""
        CREATE TABLE IF NOT EXISTS classes_outils (
            classe_id   INTEGER PRIMARY KEY AUTOINCREMENT,
            ref         TEXT UNIQUE NOT NULL,
            nom         TEXT NOT NULL,
            description TEXT,
            sort_order  INTEGER DEFAULT 0,
            created_at  INTEGER DEFAULT (strftime('%s', 'now'))
        )
    """)
    conn.executemany(
        "INSERT OR IGNORE INTO classes_outils (ref, nom, description, sort_order) VALUES (?,?,?,?)",
        [
            ("language", "Languages",       "Interpréteurs et compilateurs",            10),
            ("dev-tool", "Dev Tools",       "Outils de développement",                 20),
            ("ide",      "IDEs",            "Environnements de développement intégrés", 30),
            ("chat-llm", "Chat LLM",        "Interfaces de chat avec les LLM",         40),
            ("agent",    "Agents",          "Orchestrateurs IA autonomes",             50),
            ("engine",   "LLM Engines",     "Moteurs d'exécution locale de LLM",       60),
            ("router",   "Routers",         "Passerelles et proxy LLM",               70),
            ("context",  "Context Tools",   "Gestion du contexte et secrets",          80),
            ("system",   "System Tools",    "Utilitaires système",                     90),
            ("other",    "Other",           "Autres outils",                           999),
        ],
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_classes_outils_ref ON classes_outils(ref)"
    )


def _add_column_if_missing(conn, table: str, column: str, decl: str) -> None:
    """ALTER TABLE idempotent : ajoute `column` à `table` si elle n'existe pas.

    `decl` est la définition SQL de la colonne (ex: 'INTEGER REFERENCES ...').
    """
    cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")


def resolve_classe_id(conn, classe_ref: Optional[str]) -> Optional[int]:
    """Traduit une ref de classe (ex: 'agent') en classe_id.

    Retourne None si classe_ref est None/empty ou si la classe n'existe pas.
    Crée la table classes_outils si absente (résilience).
    """
    if not classe_ref:
        return None
    _ensure_classes_outils_table(conn)
    row = conn.execute(
        "SELECT classe_id FROM classes_outils WHERE ref=?", (classe_ref,)
    ).fetchone()
    return row[0] if row else None


def _default_class_for_ref(ref: str) -> str:
    """Retourne la classe métier par défaut pour un ref, 'other' sinon."""
    return _DEFAULT_CLASS_MAP.get(ref, "other")


# ──────────────────────────────────────────────
#  Migrations ModelWeaverDB
# ──────────────────────────────────────────────

class ModelWeaverMigrations:
    @staticmethod
    def apply(conn: sqlite3.Connection) -> None:
        base = Path(__file__).resolve().parent

        # Schéma de base
        schema = base / "modelweaver_schema.sql"
        if schema.exists():
            conn.executescript(schema.read_text())

        # Migration: ajouter key_display à api_keys
        try:
            conn.execute("ALTER TABLE api_keys ADD COLUMN key_display TEXT")
        except Exception:
            conn.rollback()

        # Migration: ajouter locked à api_keys
        try:
            conn.execute("ALTER TABLE api_keys ADD COLUMN locked INTEGER DEFAULT 0")
        except Exception:
            conn.rollback()

        # Table d'état système
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                os TEXT,
                architecture TEXT,
                os_version TEXT,
                detected_managers TEXT,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # tool_usage : état d'install local par machine
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                install_id TEXT,
                outil_ref TEXT,
                version_ref TEXT,
                recette_id INTEGER,
                etat TEXT CHECK(etat IN ('installed','uninstalled','upgraded')),
                ts INTEGER DEFAULT (strftime('%s','now'))
            )
        """)

        # ── Migration classes_outils (taxonomie métier) ──
        try:
            _ensure_classes_outils_table(conn)
            _add_column_if_missing(
                conn, "local_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_local_outils_classe "
                    "ON local_outils(classe_outil_id)"
                )
            except Exception:
                pass
            # Backfill : tout outil local sans classe reçoit la classe par défaut
            for row in conn.execute(
                "SELECT local_outil_id, outil_ref FROM local_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                cid = resolve_classe_id(conn, _default_class_for_ref(row["outil_ref"]))
                if cid is not None:
                    conn.execute(
                        "UPDATE local_outils SET classe_outil_id=? WHERE local_outil_id=?",
                        (cid, row["local_outil_id"]),
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration classes_outils (local) ignorée: {e}")

        # Commit final : ferme la transaction implicite du DDL ci-dessus
        try:
            conn.commit()
        except Exception:
            conn.rollback()


# ──────────────────────────────────────────────
#  Migrations CatalogueDB
# ──────────────────────────────────────────────

class CatalogueMigrations:
    @staticmethod
    def apply(conn: sqlite3.Connection) -> None:
        base = Path(__file__).resolve().parent
        schema = base / "catalogue_schema.sql"

        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalogue_providers'"
        )
        if not cur.fetchone():
            if schema.exists():
                conn.executescript(schema.read_text())
        else:
            # Migration: ajoute provider_models si manquant
            try:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS provider_models (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                        model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                        provider_model_name TEXT NOT NULL,
                        context_window_tokens INTEGER,
                        max_output_tokens INTEGER,
                        cost_per_input_token TEXT,
                        cost_per_output_token TEXT,
                        cost_per_thinking_token TEXT,
                        status TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','experimental')),
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(provider_id, model_id))
                """)
            except Exception:
                conn.rollback()

            # Migration: nouvelles tables catalogue outils/versions/recettes/popularité
            try:
                conn.executescript("""
                    CREATE TABLE IF NOT EXISTS catalogue_outils (
                        outil_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        ref TEXT UNIQUE NOT NULL, nom TEXT NOT NULL,
                        fabricant TEXT, description TEXT,
                        tool_type TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
                        created_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS catalogue_versions (
                        version_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        outil_id INTEGER NOT NULL REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nom_version TEXT NOT NULL, description TEXT,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        UNIQUE(outil_id, nom_version));
                    CREATE TABLE IF NOT EXISTS catalogue_recettes (
                        recette_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        version_id INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
                        os TEXT NOT NULL DEFAULT 'all', arch TEXT NOT NULL DEFAULT 'all',
                        manager TEXT, package TEXT, confidence REAL DEFAULT 1.0,
                        createur_id TEXT DEFAULT 'system',
                        install_count INTEGER DEFAULT 0, uninstall_count INTEGER DEFAULT 0,
                        content TEXT, enabled INTEGER DEFAULT 1,
                        created_at INTEGER DEFAULT (strftime('%s','now')),
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE TABLE IF NOT EXISTS outils_popularite (
                        outil_id INTEGER PRIMARY KEY REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
                        nb_install INTEGER DEFAULT 0, nb_desinstall INTEGER DEFAULT 0,
                        updated_at INTEGER DEFAULT (strftime('%s','now')));
                    CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
                    CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
                """)
            except Exception:
                conn.rollback()

            # Migration: ajouter tool_type aux DB existantes
            try:
                conn.execute("ALTER TABLE catalogue_outils ADD COLUMN tool_type TEXT")
            except Exception:
                conn.rollback()

        # ── Migration classes_outils (taxonomie métier) ──
        try:
            _ensure_classes_outils_table(conn)
            _add_column_if_missing(
                conn, "catalogue_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outils_classe "
                    "ON catalogue_outils(classe_outil_id)"
                )
            except Exception:
                conn.rollback()
            # Backfill : tout outil sans classe_outil_id reçoit la classe par défaut
            for row in conn.execute(
                "SELECT outil_id, ref FROM catalogue_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                classe_ref = _default_class_for_ref(row["ref"])
                cid = resolve_classe_id(conn, classe_ref)
                if cid is not None:
                    conn.execute(
                        "UPDATE catalogue_outils SET classe_outil_id=? WHERE outil_id=?",
                        (cid, row["outil_id"]),
                    )
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration classes_outils ignorée: {e}")

        # ── Migration catalogue_aliases (réconciliation noms externes) ──
        try:
            cur = conn.execute("PRAGMA table_info(catalogue_aliases)").fetchall()
            cols = {row[1] for row in cur}
            if cur and "target" not in cols:
                conn.execute("DROP TABLE IF EXISTS catalogue_aliases")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS catalogue_aliases (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    source        TEXT NOT NULL,
                    target        TEXT NOT NULL,
                    scope         TEXT NOT NULL CHECK(scope IN ('provider','model','provider-model')),
                    alias         TEXT NOT NULL,
                    canonical_ref TEXT NOT NULL,
                    priority      INTEGER DEFAULT 0,
                    created_at    INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(source, target, scope, alias)
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_target_scope "
                "ON catalogue_aliases(target, scope)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_canonical "
                "ON catalogue_aliases(canonical_ref)"
            )
            conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration catalogue_aliases ignorée: {e}")

        # ── Seed si tables vides ──
        try:
            pc = conn.execute("SELECT COUNT(*) FROM catalogue_providers").fetchone()[0]
            ec = 0
            try:
                ec = conn.execute("SELECT COUNT(*) FROM provider_endpoints").fetchone()[0]
            except Exception:
                ec = 0
            if (pc == 0 or ec == 0) and schema.exists():
                conn.executescript(schema.read_text())
                conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Seed ignoré: {e}")

        # ── Migration colonnes provider_endpoints ──
        try:
            _add_column_if_missing(conn, "provider_endpoints", "local_latency", "REAL")
            _add_column_if_missing(conn, "provider_endpoints", "global_quality", "REAL")
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration provider_endpoints ignorée: {e}")

        # ── Migration context_window_effective + context_audit_log ──
        try:
            _add_column_if_missing(conn, "provider_models", "context_window_effective", "INTEGER")
            _add_column_if_missing(conn, "provider_models", "available", "INTEGER DEFAULT 1")
            _add_column_if_missing(conn, "provider_models", "free_tier", "INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "provider_models", "cost_per_thinking_token", "TEXT")
            _add_column_if_missing(conn, "provider_models", "unavailable", "INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "provider_models", "noretryuntil", "REAL DEFAULT 0")
            _add_column_if_missing(conn, "provider_models", "notrytime", "REAL DEFAULT 0")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS context_audit_log (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref          TEXT NOT NULL,
                    model_ref             TEXT NOT NULL,
                    tokens_sent           INTEGER NOT NULL,
                    detected_context_limit INTEGER,
                    context_window_effective INTEGER,
                    created_at            INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_provider_model "
                "ON context_audit_log(provider_ref, model_ref)"
            )
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration context_audit_log ignorée: {e}")

        # ── Migration : journal des appels LLM réels ──
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_call_log (
                    id               INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id      INTEGER NOT NULL,
                    model_id         INTEGER NOT NULL,
                    provider_model_id INTEGER,
                    agent_id         TEXT,
                    success          INTEGER NOT NULL DEFAULT 1,
                    tokens_in        INTEGER DEFAULT 0,
                    tokens_out       INTEGER DEFAULT 0,
                    tokens_thinking  INTEGER DEFAULT 0,
                    latency_ms       REAL DEFAULT 0,
                    error_code       TEXT,
                    error_msg        TEXT,
                    call_type        TEXT DEFAULT 'chat',
                    created_at       INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            _add_column_if_missing(conn, "model_call_log", "agent_id", "TEXT")
            _add_column_if_missing(conn, "model_call_log", "error_msg", "TEXT")
            _add_column_if_missing(conn, "model_call_log", "call_type", "TEXT DEFAULT 'chat'")
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_provider_model "
                "ON model_call_log(provider_id, model_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_agent "
                "ON model_call_log(agent_id, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_created "
                "ON model_call_log(created_at, id)"
            )
            # Archive des lignes purgées
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_call_log_archive (
                    id               INTEGER PRIMARY KEY,
                    provider_id      INTEGER NOT NULL,
                    model_id         INTEGER NOT NULL,
                    provider_model_id INTEGER,
                    agent_id         TEXT,
                    success          INTEGER NOT NULL DEFAULT 1,
                    tokens_in        INTEGER DEFAULT 0,
                    tokens_out       INTEGER DEFAULT 0,
                    tokens_thinking  INTEGER DEFAULT 0,
                    latency_ms       REAL DEFAULT 0,
                    error_code       TEXT,
                    error_msg        TEXT,
                    call_type        TEXT DEFAULT 'chat',
                    created_at       INTEGER DEFAULT (strftime('%s', 'now')),
                    archived_at      INTEGER DEFAULT (strftime('%s', 'now'))
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_created "
                "ON model_call_log_archive(created_at, id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_model "
                "ON model_call_log_archive(provider_id, model_id, id)"
            )
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration model_call_log ignorée: {e}")

        # ── Migration : nouvelles tables d'acces modeles (V0.7.0.4+) ──
        try:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS key_endpoint_models (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    key_ref TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    provider_model_name TEXT NOT NULL,
                    declared INTEGER DEFAULT 0,
                    available INTEGER DEFAULT 0,
                    last_checked_at INTEGER,
                    last_error TEXT,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(endpoint_id, key_ref, model_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_efficacy (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    use_case TEXT NOT NULL,
                    score_quality REAL DEFAULT 0, score_speed REAL DEFAULT 0,
                    score_cost REAL DEFAULT 0, score_reliability REAL DEFAULT 0,
                    samples INTEGER DEFAULT 0,
                    updated_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id, use_case)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_provider_scoring (
                    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id           INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    model_id              INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    endpoint_id           INTEGER REFERENCES provider_endpoints(endpoint_id) ON DELETE SET NULL,
                    latency_avg_ms        REAL DEFAULT 0,
                    latency_p95_ms        REAL DEFAULT 0,
                    latency_stddev_ms     REAL DEFAULT 0,
                    latency_samples       INTEGER DEFAULT 0,
                    quality_score         REAL DEFAULT 0,
                    score_chat            REAL DEFAULT 0,
                    score_coding          REAL DEFAULT 0,
                    score_reasoning       REAL DEFAULT 0,
                    score_knowledge       REAL DEFAULT 0,
                    score_agentic         REAL DEFAULT 0,
                    cost_per_1k_input     REAL DEFAULT 0,
                    cost_per_1k_output    REAL DEFAULT 0,
                    global_score          REAL DEFAULT 0,
                    is_synthetic          INTEGER DEFAULT 0,
                    benchmark_ref         TEXT DEFAULT '',
                    last_scored_at        INTEGER DEFAULT (strftime('%s','now')),
                    confidence            REAL DEFAULT 0,
                    created_at            INTEGER DEFAULT (strftime('%s','now')),
                    updated_at            INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(provider_id, model_id)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                    unit TEXT, scope TEXT
                )
            """)
            conn.execute("""
                INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
                    ('req_per_min','Requetes / minute','requests','requests'),
                    ('req_per_day','Requetes / jour','requests','requests'),
                    ('tok_per_min','Tokens / minute','tokens','tokens'),
                    ('tok_per_hour','Tokens / heure','tokens','tokens'),
                    ('tok_per_day','Tokens / jour','tokens','tokens'),
                    ('cost_per_day','Cout / jour (USD)','usd','cost'),
                    ('cost_per_month','Cout / mois (USD)','usd','cost')
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS budgets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    target_type TEXT NOT NULL CHECK(target_type IN ('provider','model','endpoint','key')),
                    target_ref TEXT NOT NULL,
                    tag_id INTEGER NOT NULL REFERENCES budget_tags(id),
                    limit_value REAL NOT NULL,
                    window TEXT NOT NULL DEFAULT 'day' CHECK(window IN ('minute','hour','day','month')),
                    cost_per_unit REAL,
                    created_at INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_probe_state (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_id INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
                    endpoint_id INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
                    key_ref TEXT NOT NULL,
                    model_id INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
                    consecutive_failures INTEGER DEFAULT 0,
                    backoff_s INTEGER DEFAULT 0,
                    next_probe_at INTEGER DEFAULT 0,
                    last_status TEXT,
                    last_error TEXT,
                    last_probed_at INTEGER,
                    defunct INTEGER DEFAULT 0,
                    created_at INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(endpoint_id, key_ref, model_id)
                )
            """)
        except Exception as e:
            import sys as _sys
            conn.rollback()
            print(f"⚠️  Migration tables d'acces ignoree: {e}", file=_sys.stderr)

        # ── Seed modèles + provider_models si vides ──
        try:
            mc = conn.execute("SELECT COUNT(*) FROM catalogue_models").fetchone()[0]
            pmc = 0
            try:
                pmc = conn.execute("SELECT COUNT(*) FROM provider_models").fetchone()[0]
            except Exception:
                pmc = 0
            if mc == 0 or pmc == 0:
                from modules.llm_manager.llm_manager import seed_models, seed_provider_models
                if mc == 0:
                    seed_models(conn)
                    conn.commit()
                if pmc == 0:
                    seed_provider_models(conn)
                    conn.commit()
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Seed modèles ignoré: {e}")

        # Commit final
        try:
            conn.commit()
        except Exception:
            conn.rollback()


# ──────────────────────────────────────────────
#  Migrations RuntimeDB
# ──────────────────────────────────────────────

class RuntimeMigrations:
    @staticmethod
    def apply(conn: sqlite3.Connection) -> None:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS processes (
                id INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                pid INTEGER,
                parent_id INTEGER,
                status TEXT,
                command TEXT,
                log_path TEXT,
                cpu REAL,
                rss_kb INTEGER,
                started_at INTEGER,
                ended_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS services (
                name TEXT PRIMARY KEY,
                mode TEXT,
                command TEXT,
                args TEXT,
                status TEXT,
                pid INTEGER,
                parent TEXT,
                restart INTEGER,
                restarts INTEGER DEFAULT 0,
                last_exit INTEGER,
                started_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS install_jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ref TEXT NOT NULL,
                name TEXT,
                job_type TEXT,
                status TEXT,
                log TEXT,
                pid INTEGER,
                created_at INTEGER,
                updated_at INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── Migration : tables usage & mesure locales (V0.7.0.4+) ──
        try:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS real_call_models (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref  TEXT,
                    endpoint_id   INTEGER,
                    key_ref       TEXT,
                    model_ref     TEXT,
                    agent_id      TEXT,
                    sent_at       INTEGER NOT NULL,
                    received_at   INTEGER,
                    tokens_in     INTEGER DEFAULT 0,
                    tokens_out    INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost          REAL DEFAULT 0,
                    status        TEXT CHECK(status IN ('ok','rate_limited','error','quota_exhausted')),
                    error_code    TEXT,
                    error_detail  TEXT,
                    window_key    TEXT,
                    created_at    INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_rcm_model ON real_call_models(model_ref);
                CREATE INDEX IF NOT EXISTS idx_rcm_sent ON real_call_models(sent_at);
                CREATE INDEX IF NOT EXISTS idx_rcm_status ON real_call_models(status);

                CREATE TABLE IF NOT EXISTS really_used_budget (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_tag_code TEXT NOT NULL,
                    target_type   TEXT NOT NULL,
                    target_ref    TEXT NOT NULL,
                    window        TEXT,
                    measured_limit REAL,
                    sample_count  INTEGER DEFAULT 0,
                    first_exhausted_at INTEGER,
                    confidence    REAL DEFAULT 0,
                    method        TEXT,
                    measured_at   INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(budget_tag_code, target_type, target_ref, window)
                );

                CREATE TABLE IF NOT EXISTS endpoint_model_usage (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint_id   INTEGER,
                    model_ref     TEXT,
                    agent_id      TEXT,
                    requests      INTEGER DEFAULT 0,
                    tokens_in     INTEGER DEFAULT 0,
                    tokens_out    INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost          REAL DEFAULT 0,
                    last_call_at  INTEGER,
                    last_call_working INTEGER DEFAULT 1,
                    error_count   INTEGER DEFAULT 0,
                    created_at    INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_emu_endpoint ON endpoint_model_usage(endpoint_id);
                CREATE INDEX IF NOT EXISTS idx_emu_model ON endpoint_model_usage(model_ref);

                CREATE TABLE IF NOT EXISTS budget_consumption (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    budget_id     INTEGER NOT NULL,
                    used          REAL DEFAULT 0,
                    updated_at    INTEGER DEFAULT (strftime('%s','now'))
                );

                CREATE TABLE IF NOT EXISTS local_model_efficacy (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_ref     TEXT NOT NULL,
                    use_case      TEXT NOT NULL,
                    score_quality   REAL DEFAULT 0,
                    score_speed     REAL DEFAULT 0,
                    score_cost      REAL DEFAULT 0,
                    score_reliability REAL DEFAULT 0,
                    samples       INTEGER DEFAULT 0,
                    criteria_meta TEXT,
                    last_evaluated_at INTEGER,
                    UNIQUE(model_ref, use_case)
                );

                CREATE TABLE IF NOT EXISTS agent_actif (
                    agent_id        TEXT PRIMARY KEY,
                    status          TEXT,
                    current_step    TEXT,
                    last_heartbeat  INTEGER,
                    calls_count     INTEGER DEFAULT 0,
                    tokens_total    INTEGER DEFAULT 0,
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_agent_actif_hb ON agent_actif(last_heartbeat);

                CREATE TABLE IF NOT EXISTS usage_history_1m (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1m_bucket ON usage_history_1m(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1h (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1h_bucket ON usage_history_1h(bucket);
            """)
        except Exception as e:
            conn.rollback()
            print(f"⚠️  Migration tables usage ignorée: {e}")

        # Migration V0.8.6 : tokens de raisonnement (thinking) dans l'usage
        try:
            _add_column_if_missing(conn, "real_call_models",
                                   "tokens_thinking", "INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "endpoint_model_usage",
                                   "tokens_thinking", "INTEGER DEFAULT 0")
            _add_column_if_missing(conn, "real_call_models",
                                   "rolled_at", "INTEGER")
        except Exception:
            conn.rollback()

        conn.commit()


# ──────────────────────────────────────────────
#  Migrations AgentsDB
# ──────────────────────────────────────────────

class AgentsMigrations:
    @staticmethod
    def apply(conn: sqlite3.Connection) -> None:
        base = Path(__file__).resolve().parent
        schema = base / "agents_schema.sql"
        if schema.exists():
            conn.executescript(schema.read_text())

        _add_column_if_missing(conn, "agents", "storage_json", "TEXT")
        _add_column_if_missing(conn, "agent_signals", "source_agent_id", "INTEGER")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS wait_for (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id    INTEGER NOT NULL,
                condition   TEXT NOT NULL,
                status      TEXT DEFAULT 'waiting',
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                ready_at    TEXT,
                expires_at  TEXT
            )
        """)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wait_for_status "
            "ON wait_for(status, agent_id)"
        )


# ──────────────────────────────────────────────
#  MigrationManager — interface publique
# ──────────────────────────────────────────────

class MigrationManager:
    """Applique les migrations sur une connexion existante.

    Usage:
        manager = MigrationManager(db_path)
        manager.apply_modelweaver(conn)
        manager.apply_catalogue(conn)
        manager.apply_runtime(conn)
        manager.apply_agents(conn)
        manager.apply_migrations(migrations_dir)
    """

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)

    def apply_modelweaver(self, conn: sqlite3.Connection) -> None:
        ModelWeaverMigrations.apply(conn)

    def apply_catalogue(self, conn: sqlite3.Connection) -> None:
        CatalogueMigrations.apply(conn)

    def apply_runtime(self, conn: sqlite3.Connection) -> None:
        RuntimeMigrations.apply(conn)

    def apply_agents(self, conn: sqlite3.Connection) -> None:
        AgentsMigrations.apply(conn)

    def apply_migrations(self, migrations_dir: Path) -> None:
        """Exécute tous les fichiers SQL du dossier `migrations_dir` dans l'ordre."""
        if not migrations_dir.exists():
            return
        files = sorted(migrations_dir.glob("*.sql"))
        for sql_file in files:
            try:
                sql = sql_file.read_text(encoding="utf-8")
                self.db_path.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
                conn.executescript(sql)
                conn.commit()
                conn.close()
            except Exception as e:
                print(f"⚠️  Migration {sql_file.name} ignorée: {e}")
