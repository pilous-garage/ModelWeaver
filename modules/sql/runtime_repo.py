#!/usr/bin/env python3
"""Runtime — DB runtime des agents.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Contient : RuntimeDB + helpers associés.
"""

import json
import sqlite3
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from modules.sql.schema import (
    _row_to_dict, _rows_to_list, _add_column_if_missing, _default_runtime_db,
)
from services._common import mw_home


class RuntimeDB:
    """DB isolée pour les données runtime : processus, services, jobs d'install.

    Le GUI (Rust) y écrit directement en haute fréquence (mirror processus/
    services) ; le daemon et l'installer_worker écrivent aussi install_jobs.
    Isolée de l'inventaire/catalogue pour éviter la contention SQLite.
    Séparation physique : la GUI ne poll PAS table par table, elle compare
    `PRAGMA data_version` de cette DB (voir `read_db_version`).
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path or _default_runtime_db())
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema()

    def _ensure_schema(self):
        self.conn.execute("""
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
        self.conn.execute("""
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
        self.conn.execute("""
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
        # meta : clés de signal hors-table (ex: 'dependencies' pour le refresh GUI)
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value INTEGER NOT NULL DEFAULT 0
            )
        """)

        # ── Migration : tables usage & mesure locales (V0.7.0.4+) ──
        # modelweaver.db privé, jamais poussé au distant.
        try:
            self.conn.executescript("""
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

                -- Historique en CASCADE (moniteur LLM) : le batcheur agrège
                -- model_call_log (détail) vers usage_history_1m, puis chaque
                -- niveau agrège le précédent (15m → 3h → 1d → 1w → 1mo).
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
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1m_bucket ON usage_history_1m(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_15m (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh15m_bucket ON usage_history_15m(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_3h (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh3h_bucket ON usage_history_3h(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1d (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1d_bucket ON usage_history_1d(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1w (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1w_bucket ON usage_history_1w(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1mo (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1mo_bucket ON usage_history_1mo(bucket);

                -- Séquences de réussite par (provider, model) : période continue
                -- de succès entre deux échecs. Le batcheur ouvre/ferme les
                -- séquences. Servira au calibrage rate_limit (p10/p90 req/min)
                -- et au budget d'allocation. Pas par agent (global modèle).
                CREATE TABLE IF NOT EXISTS model_success_runs (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    provider_ref    TEXT NOT NULL,
                    model_ref       TEXT NOT NULL,
                    seq_start       INTEGER NOT NULL,
                    seq_end         INTEGER,
                    duration_s      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    avg_latency_ms  REAL DEFAULT 0,
                    status          TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_msr_model ON model_success_runs(provider_ref, model_ref);
                CREATE INDEX IF NOT EXISTS idx_msr_status ON model_success_runs(status);
                CREATE INDEX IF NOT EXISTS idx_msr_seqend ON model_success_runs(seq_end);
            """)
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration tables usage ignorée: {e}")

        # Migration V0.8.6 : tokens de raisonnement (thinking) dans l'usage
        try:
            _add_column_if_missing(self.conn, "real_call_models",
                                   "tokens_thinking", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "endpoint_model_usage",
                                   "tokens_thinking", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "real_call_models",
                                   "rolled_at", "INTEGER")
        except Exception:
            self.conn.rollback()

        # Migration batchage cascade : first_call / last_call (bornes temporelles
        # du batch) sur chaque table d'agrégats — pour afficher « dernier call ».
        try:
            for _t in ("usage_history_1m", "usage_history_15m",
                       "usage_history_3h", "usage_history_1d",
                       "usage_history_1w", "usage_history_1mo"):
                _add_column_if_missing(self.conn, _t, "first_call", "INTEGER")
                _add_column_if_missing(self.conn, _t, "last_call", "INTEGER")
        except Exception:
            self.conn.rollback()

        self.conn.commit()

    def data_version(self) -> int:
        return read_db_version(self.conn)

    def bump_meta(self, key: str, commit: bool = True):
        bump_meta(self.conn, key, commit=commit)

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  AgentsDB — Base dédiée aux agents
# ──────────────────────────────────────────────

