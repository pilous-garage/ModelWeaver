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
                    adresse_id    INTEGER,
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

                CREATE TABLE IF NOT EXISTS score_thinking_power (
                    -- INDICE DE PUISSANCE DE PENSÉE du modèle (capacité cognitive,
                    -- différencie un modèle frontière d'un bas niveau). PAS les
                    -- tokens de raisonnement.
                    -- INIT : score_reasoning (le benchmark de raisonnement). À
                    -- terme : mis à jour par les scores d'expérience (Idée 18) —
                    -- pas encore.
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    model_id      INTEGER NOT NULL,     -- catalogue_models.id (canonique)
                    score_thinking REAL DEFAULT 0,
                    samples       INTEGER DEFAULT 0,
                    updated_at    INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(model_id)
                );
                CREATE INDEX IF NOT EXISTS idx_stp_model ON score_thinking_power(model_id);

                CREATE TABLE IF NOT EXISTS endpoint_model_usage (
                    id            INTEGER PRIMARY KEY AUTOINCREMENT,
                    endpoint_id   INTEGER,
                    model_ref     TEXT,
                    adresse_id    INTEGER,
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
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1m_bucket ON usage_history_1m(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_15m (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh15m_bucket ON usage_history_15m(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_3h (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh3h_bucket ON usage_history_3h(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1d (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1d_bucket ON usage_history_1d(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1w (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    first_call      INTEGER,
                    last_call       INTEGER,
                    UNIQUE(bucket, provider_ref, model_ref, agent_id)
                );
                CREATE INDEX IF NOT EXISTS idx_uh1w_bucket ON usage_history_1w(bucket);
                CREATE TABLE IF NOT EXISTS usage_history_1mo (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    agent_id        TEXT,
                    requests        INTEGER DEFAULT 0,
                    success_count   INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    tokens_thinking INTEGER DEFAULT 0,
                    cost            REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
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
                    adresse_id      INTEGER,
                    seq_start       INTEGER NOT NULL,
                    seq_end         INTEGER,
                    duration_s      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    avg_latency_ms  REAL DEFAULT 0,
                    req_total       INTEGER DEFAULT 0,
                    tok_total       INTEGER DEFAULT 0,
                    status          TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_msr_model ON model_success_runs(provider_ref, model_ref);
                CREATE INDEX IF NOT EXISTS idx_msr_status ON model_success_runs(status);
                CREATE INDEX IF NOT EXISTS idx_msr_seqend ON model_success_runs(seq_end);

                -- Sessions d'appels LLM PAR SOURCE (caller_id : agent:N, bridge,
                -- service:model_sync, probe…). DISTINCT de model_success_runs
                -- (qui est par provider/model au niveau GLOBAL, non interrompu).
                -- Ici on suit l'activité de CHAQUE appelant : une session s'ouvre
                -- au premier appel RÉUSSI d'un caller_id et se ferme au premier
                -- échec (ou quand une NOUVELLE session s'ouvre pour le même id —
                -- un caller ne garde qu'une session ouverte à la fois).
                CREATE TABLE IF NOT EXISTS llm_caller_sessions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    caller_id       TEXT NOT NULL,
                    provider_ref    TEXT,
                    model_ref       TEXT,
                    adresse_id      INTEGER,
                    seq_start       INTEGER NOT NULL,
                    seq_end         INTEGER,
                    duration_s      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    errors          INTEGER DEFAULT 0,
                    tokens_in       INTEGER DEFAULT 0,
                    tokens_out      INTEGER DEFAULT 0,
                    avg_latency_ms  REAL DEFAULT 0,
                    status          TEXT NOT NULL DEFAULT 'open' CHECK(status IN ('open','closed')),
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_cs_caller ON llm_caller_sessions(caller_id);
                CREATE INDEX IF NOT EXISTS idx_cs_status ON llm_caller_sessions(status);
                CREATE INDEX IF NOT EXISTS idx_cs_open ON llm_caller_sessions(caller_id, status);

                -- Rapport de traitement d'archive (réconciliation). Le batcheur
                -- note chaque lecture d'archive (frame + timestamp + warnings),
                -- sans jamais supprimer l'archive.
                CREATE TABLE IF NOT EXISTS archive_processing_report (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    start_ts        INTEGER NOT NULL,
                    end_ts          INTEGER NOT NULL,
                    processed_at    INTEGER DEFAULT (strftime('%s','now')),
                    lines_read      INTEGER DEFAULT 0,
                    warnings        TEXT DEFAULT '[]'
                );
                CREATE INDEX IF NOT EXISTS idx_apr_time ON archive_processing_report(processed_at);

                -- Blocs glissants de score par (provider, model) : totaux
                -- par bucket (requests, fail_count, total_latency_ms) servant à
                -- calculer fail_rate + latence des fenêtres 5m/1h/1j/1w sans
                -- rescanner le détail à chaque tick. Le batcheur ne calcule que
                -- le bloc le plus récent (tracking meta score_batch.last_5m/1h/1d)
                -- et les blocs supérieurs somment les blocs inférieurs.
                CREATE TABLE IF NOT EXISTS score_batch_blocks_5m (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT NOT NULL,
                    model_ref       TEXT NOT NULL,
                    adresse_id      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    fail_count      INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    UNIQUE(bucket, provider_ref, model_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_sbb5m_bucket ON score_batch_blocks_5m(bucket);
                CREATE TABLE IF NOT EXISTS score_batch_blocks_1h (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT NOT NULL,
                    model_ref       TEXT NOT NULL,
                    adresse_id      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    fail_count      INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    UNIQUE(bucket, provider_ref, model_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_sbb1h_bucket ON score_batch_blocks_1h(bucket);
                CREATE TABLE IF NOT EXISTS score_batch_blocks_1d (
                    bucket          INTEGER NOT NULL,
                    provider_ref    TEXT NOT NULL,
                    model_ref       TEXT NOT NULL,
                    adresse_id      INTEGER,
                    requests        INTEGER DEFAULT 0,
                    fail_count      INTEGER DEFAULT 0,
                    total_latency_ms REAL DEFAULT 0,
                    UNIQUE(bucket, provider_ref, model_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_sbb1d_bucket ON score_batch_blocks_1d(bucket);

                -- Scores batch par (provider, model) : fail_rate + latence des
                -- 4 fenêtres, composées depuis les blocs stockés. fr = 0 si aucun
                -- appel (pas d'échec → score parfait). lat = moyenne pondérée.
                CREATE TABLE IF NOT EXISTS score_batch (
                    provider_ref    TEXT NOT NULL,
                    model_ref       TEXT NOT NULL,
                    adresse_id      INTEGER,
                    requests_5m     INTEGER DEFAULT 0,
                    requests_1h     INTEGER DEFAULT 0,
                    requests_1j     INTEGER DEFAULT 0,
                    requests_1w     INTEGER DEFAULT 0,
                    fail_count_5m   INTEGER DEFAULT 0,
                    fail_count_1h   INTEGER DEFAULT 0,
                    fail_count_1j   INTEGER DEFAULT 0,
                    fail_count_1w   INTEGER DEFAULT 0,
                    total_lat_ms_5m REAL DEFAULT 0,
                    total_lat_ms_1h REAL DEFAULT 0,
                    total_lat_ms_1j REAL DEFAULT 0,
                    total_lat_ms_1w REAL DEFAULT 0,
                    fr_5m           REAL DEFAULT 0,
                    fr_1h           REAL DEFAULT 0,
                    fr_1j           REAL DEFAULT 0,
                    fr_1w           REAL DEFAULT 0,
                    lat_5m_ms       REAL DEFAULT 0,
                    lat_1h_ms       REAL DEFAULT 0,
                    lat_1j_ms       REAL DEFAULT 0,
                    lat_1w_ms       REAL DEFAULT 0,
                    score_fail_rate REAL DEFAULT 0,
                    score_latency   REAL DEFAULT 0,
                    score_etire     REAL DEFAULT 0,
                    score_final     REAL DEFAULT 0,
                    score_last_5    REAL DEFAULT 0,
                    score_1h        REAL DEFAULT 0,
                    score_1d        REAL DEFAULT 0,
                    score_1w        REAL DEFAULT 0,
                    score_all       REAL DEFAULT 0,
                    updated_at      INTEGER DEFAULT (strftime('%s','now')),
                    UNIQUE(provider_ref, model_ref)
                );
                CREATE INDEX IF NOT EXISTS idx_sbatch_model ON score_batch(provider_ref, model_ref);

                -- ── Buckets STABLES de compteurs (succ/tot) par modèle ──────────
                -- Refonte (V0.16) : on stocke des COMPTEURS nb_success / nb_fail
                -- par bucket (jamais des scores — un bucket vide ≠ succès). Les
                -- scores de zone sont calculés à la demande par somme :
                --   score_durée = sqrt((1+succ)/(1+tot)) — chacun ∈ [0,1]
                --   score_1h = f(Σ 12×5m), score_1d = f(Σ 12×5m + Σ 23×1h),
                --   score_1w = f(Σ 12×5m + Σ 23×1h + Σ 6×1d), score_all = f(cumul)
                -- score_last_5 = f(5 dernières min, log direct)
                -- score_total (0–1) = moyenne des 5 scores de zone.
                CREATE TABLE IF NOT EXISTS model_bucket_counts (
                    provider_ref TEXT NOT NULL,
                    model_ref    TEXT NOT NULL,
                    adresse_id   INTEGER,
                    s5_succ_0 INTEGER DEFAULT 0, s5_tot_0 INTEGER DEFAULT 0,
                    s5_succ_1 INTEGER DEFAULT 0, s5_tot_1 INTEGER DEFAULT 0,
                    s5_succ_2 INTEGER DEFAULT 0, s5_tot_2 INTEGER DEFAULT 0,
                    s5_succ_3 INTEGER DEFAULT 0, s5_tot_3 INTEGER DEFAULT 0,
                    s5_succ_4 INTEGER DEFAULT 0, s5_tot_4 INTEGER DEFAULT 0,
                    s5_succ_5 INTEGER DEFAULT 0, s5_tot_5 INTEGER DEFAULT 0,
                    s5_succ_6 INTEGER DEFAULT 0, s5_tot_6 INTEGER DEFAULT 0,
                    s5_succ_7 INTEGER DEFAULT 0, s5_tot_7 INTEGER DEFAULT 0,
                    s5_succ_8 INTEGER DEFAULT 0, s5_tot_8 INTEGER DEFAULT 0,
                    s5_succ_9 INTEGER DEFAULT 0, s5_tot_9 INTEGER DEFAULT 0,
                    s5_succ_10 INTEGER DEFAULT 0, s5_tot_10 INTEGER DEFAULT 0,
                    s5_succ_11 INTEGER DEFAULT 0, s5_tot_11 INTEGER DEFAULT 0,
                    sh_succ_0 INTEGER DEFAULT 0, sh_tot_0 INTEGER DEFAULT 0,
                    sh_succ_1 INTEGER DEFAULT 0, sh_tot_1 INTEGER DEFAULT 0,
                    sh_succ_2 INTEGER DEFAULT 0, sh_tot_2 INTEGER DEFAULT 0,
                    sh_succ_3 INTEGER DEFAULT 0, sh_tot_3 INTEGER DEFAULT 0,
                    sh_succ_4 INTEGER DEFAULT 0, sh_tot_4 INTEGER DEFAULT 0,
                    sh_succ_5 INTEGER DEFAULT 0, sh_tot_5 INTEGER DEFAULT 0,
                    sh_succ_6 INTEGER DEFAULT 0, sh_tot_6 INTEGER DEFAULT 0,
                    sh_succ_7 INTEGER DEFAULT 0, sh_tot_7 INTEGER DEFAULT 0,
                    sh_succ_8 INTEGER DEFAULT 0, sh_tot_8 INTEGER DEFAULT 0,
                    sh_succ_9 INTEGER DEFAULT 0, sh_tot_9 INTEGER DEFAULT 0,
                    sh_succ_10 INTEGER DEFAULT 0, sh_tot_10 INTEGER DEFAULT 0,
                    sh_succ_11 INTEGER DEFAULT 0, sh_tot_11 INTEGER DEFAULT 0,
                    sh_succ_12 INTEGER DEFAULT 0, sh_tot_12 INTEGER DEFAULT 0,
                    sh_succ_13 INTEGER DEFAULT 0, sh_tot_13 INTEGER DEFAULT 0,
                    sh_succ_14 INTEGER DEFAULT 0, sh_tot_14 INTEGER DEFAULT 0,
                    sh_succ_15 INTEGER DEFAULT 0, sh_tot_15 INTEGER DEFAULT 0,
                    sh_succ_16 INTEGER DEFAULT 0, sh_tot_16 INTEGER DEFAULT 0,
                    sh_succ_17 INTEGER DEFAULT 0, sh_tot_17 INTEGER DEFAULT 0,
                    sh_succ_18 INTEGER DEFAULT 0, sh_tot_18 INTEGER DEFAULT 0,
                    sh_succ_19 INTEGER DEFAULT 0, sh_tot_19 INTEGER DEFAULT 0,
                    sh_succ_20 INTEGER DEFAULT 0, sh_tot_20 INTEGER DEFAULT 0,
                    sh_succ_21 INTEGER DEFAULT 0, sh_tot_21 INTEGER DEFAULT 0,
                    sh_succ_22 INTEGER DEFAULT 0, sh_tot_22 INTEGER DEFAULT 0,
                    sd_succ_0 INTEGER DEFAULT 0, sd_tot_0 INTEGER DEFAULT 0,
                    sd_succ_1 INTEGER DEFAULT 0, sd_tot_1 INTEGER DEFAULT 0,
                    sd_succ_2 INTEGER DEFAULT 0, sd_tot_2 INTEGER DEFAULT 0,
                    sd_succ_3 INTEGER DEFAULT 0, sd_tot_3 INTEGER DEFAULT 0,
                    sd_succ_4 INTEGER DEFAULT 0, sd_tot_4 INTEGER DEFAULT 0,
                    sd_succ_5 INTEGER DEFAULT 0, sd_tot_5 INTEGER DEFAULT 0,
                    -- All-time : cumul des buckets 1d sortis de la rotation
                    all_time_succ INTEGER DEFAULT 0,
                    all_time_tot  INTEGER DEFAULT 0,
                    score_1h      REAL DEFAULT 1.0,
                    score_1d      REAL DEFAULT 1.0,
                    score_1w      REAL DEFAULT 1.0,
                    score_all     REAL DEFAULT 1.0,
                    score_last_5  REAL DEFAULT 1.0,
                    score_total   REAL DEFAULT 4.0,  -- 0..4
                    updated_at    INTEGER DEFAULT (strftime('%s','now')),
                    PRIMARY KEY (provider_ref, model_ref)
                );

                -- Têtes de rotation des buckets stables.
                CREATE TABLE IF NOT EXISTS score_bucket_heads (
                    bucket_type     TEXT PRIMARY KEY,  -- '5m' | '1h' | '1d'
                    last_bucket     INTEGER DEFAULT 0, -- ts du dernier bucket calculé
                    timestamp_start INTEGER DEFAULT 0, -- début du bucket 5m courant
                    bucket_nb       INTEGER DEFAULT 0, -- écritures depuis le rollup
                    bucket_qt       INTEGER DEFAULT 0, -- seuil (12 | 23 | 6)
                    bucket_size     INTEGER DEFAULT 0  -- 300 | 3600 | 86400
                );
                INSERT OR IGNORE INTO score_bucket_heads
                    (bucket_type, bucket_qt, bucket_size)
                VALUES
                    ('5m', 12, 300), ('1h', 23, 3600), ('1d', 6, 86400);

                -- Scores benchmark ÉTIRÉS par modèle (sans provider).
                -- Étirement par min/max de colonne :
                --   score_etire = (score - min_col)/(max_col - min_col)*0.8 + 0.1
                -- Les bornes min/max UTILISÉES sont stockées dans
                -- score_benchmark_meta (une ligne par colonne) pour pouvoir
                -- appliquer le même étirement à un nouveau modèle. Si un score
                -- sort des bornes stockées, on recalcule toute la colonne.
                -- Fallback : modèle sans score spécialité → score_etire du
                -- global (src_* = 'global'), sans aucun score → baseline 0.1.
                CREATE TABLE IF NOT EXISTS score_benchmark_etire (
                    model_ref       TEXT PRIMARY KEY,
                    score_global    REAL DEFAULT 0,
                    score_etire     REAL DEFAULT 0.1,
                    score_etire_chat       REAL DEFAULT 0.1,
                    score_etire_coding     REAL DEFAULT 0.1,
                    score_etire_reasoning  REAL DEFAULT 0.1,
                    score_etire_knowledge  REAL DEFAULT 0.1,
                    score_etire_agentic    REAL DEFAULT 0.1,
                    src_chat        TEXT NOT NULL DEFAULT 'spec',
                    src_coding      TEXT NOT NULL DEFAULT 'spec',
                    src_reasoning   TEXT NOT NULL DEFAULT 'spec',
                    src_knowledge   TEXT NOT NULL DEFAULT 'spec',
                    src_agentic     TEXT NOT NULL DEFAULT 'spec',
                    is_synthetic    INTEGER DEFAULT 0,
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
                CREATE INDEX IF NOT EXISTS idx_sbe_etire ON score_benchmark_etire(score_etire);
                CREATE TABLE IF NOT EXISTS score_benchmark_meta (
                    column_name     TEXT PRIMARY KEY,
                    min_value       REAL NOT NULL,
                    max_value       REAL NOT NULL,
                    nb_models       INTEGER DEFAULT 0,
                    updated_at      INTEGER DEFAULT (strftime('%s','now'))
                );
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
                _add_column_if_missing(self.conn, _t, "req_total", "INTEGER DEFAULT 0")
                _add_column_if_missing(self.conn, _t, "tok_total", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "model_success_runs",
                                   "req_total", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "model_success_runs",
                                   "tok_total", "INTEGER DEFAULT 0")
            # V0.17 (Idée 18) : les séquences sont TYPÉES success/fail. Une run
            # s'ouvre au premier appel du type, se ferme au DERNIER appel de la
            # séquence (seq_end = nb de la séquence, JAMAIS le timestamp de
            # l'appel suivant qui change de type), puis une run du type OPPOSÉ
            # s'ouvre — l'alternance trace les rafales d'échec (rate-limit/
            # quota) que la version précédente perdait.
            _add_column_if_missing(self.conn, "model_success_runs",
                                   "seq_type", "TEXT DEFAULT 'success'")
            _add_column_if_missing(self.conn, "model_success_runs",
                                   "error_code", "TEXT DEFAULT ''")
        except Exception:
            self.conn.rollback()

        # Migration scores batch : score_etire (benchmark étiré croisé) et
        # score_final (score_etire × score_latence × (1 - score_fail_rate)).
        try:
            _add_column_if_missing(self.conn, "score_batch",
                                   "score_etire", "REAL DEFAULT 0")
            _add_column_if_missing(self.conn, "score_batch",
                                   "score_final", "REAL DEFAULT 0")
            # V0.16 : buckets stables — colonnes all_time (5e étape du score).
            _add_column_if_missing(self.conn, "model_bucket_counts",
                                   "all_time_succ", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "model_bucket_counts",
                                   "all_time_tot", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "model_bucket_counts",
                                   "score_all", "REAL DEFAULT 1.0")
            _add_column_if_missing(self.conn, "model_bucket_counts",
                                   "score_total", "REAL DEFAULT 4.0")
        except Exception:
            self.conn.rollback()

        # Migration adresse_id : clé de référence vers provider_model_address
        # (répertoire provider×model). Les tables d'usage/score/log référencent
        # désormais leur adresse par id (pas par textes).
        try:
            for _t in ("real_call_models", "endpoint_model_usage",
                       "usage_history_1m", "usage_history_15m",
                       "usage_history_3h", "usage_history_1d",
                       "usage_history_1w", "usage_history_1mo",
                       "model_success_runs", "llm_caller_sessions",
                       "score_batch_blocks_5m", "score_batch_blocks_1h",
                       "score_batch_blocks_1d", "score_batch",
                       "model_bucket_counts"):
                _add_column_if_missing(self.conn, _t, "adresse_id", "INTEGER")
        except Exception:
            self.conn.rollback()

        # Migration 5 scores : last_5 / 1h / 1d / 1w / all stockés séparément
        # (avant : fusionnés en un seul score_fail_rate).
        try:
            for _c, _d in (("score_last_5", "REAL DEFAULT 1.0"),
                           ("score_1h", "REAL DEFAULT 1.0"),
                           ("score_1d", "REAL DEFAULT 1.0"),
                           ("score_1w", "REAL DEFAULT 1.0"),
                           ("score_all", "REAL DEFAULT 1.0")):
                _add_column_if_missing(self.conn, "model_bucket_counts", _c, _d)
            for _c, _d in (("score_last_5", "REAL DEFAULT 0"),
                           ("score_1h", "REAL DEFAULT 0"),
                           ("score_1d", "REAL DEFAULT 0"),
                           ("score_1w", "REAL DEFAULT 0"),
                           ("score_all", "REAL DEFAULT 0")):
                _add_column_if_missing(self.conn, "score_batch", _c, _d)
        except Exception:
            self.conn.rollback()

        # Migration success_count : usage_history_* agrège les appels en batchs
        # mais ne gardait pas la répartition succès/échec — le scoring des
        # buckets (model_bucket_counts) ne pouvait pas reconstruire succ/tot
        # depuis les batchs. On ajoute success_count (le total = requests).
        try:
            for _t in ("usage_history_1m", "usage_history_15m",
                       "usage_history_3h", "usage_history_1d",
                       "usage_history_1w", "usage_history_1mo"):
                _add_column_if_missing(self.conn, _t, "success_count",
                                       "INTEGER DEFAULT 0")
        except Exception:
            self.conn.rollback()

        # Seed score_thinking_power : INITIALISÉ depuis score_reasoning
        # (le benchmark de raisonnement, dans catalogue.db) — indice de
        # PUISSANCE DE PENSÉE du modèle. La mise à jour par les scores
        # d'expérience viendra plus tard (Idée 18) — pas maintenant.
        try:
            _cat_path = None
            try:
                from services._common import _db_paths
                # _db_paths → (modelweaver.db, catalogue.db) — le CATALOGUE
                # est le 2e élément.
                _, _cat_path = _db_paths()
            except Exception:
                _cat_path = None
            if _cat_path:
                # ATTACH catalogue pour lire model_provider_scoring
                self.conn.execute("ATTACH DATABASE ? AS cat_scoring", (str(_cat_path),))
                try:
                    self.conn.execute("""
                        INSERT OR IGNORE INTO score_thinking_power (model_id, score_thinking, samples)
                        SELECT mps.model_id, mps.score_reasoning, 1
                        FROM cat_scoring.model_provider_scoring mps
                        WHERE mps.score_reasoning > 0
                    """)
                    self.conn.commit()
                finally:
                    try:
                        self.conn.execute("DETACH DATABASE cat_scoring")
                    except Exception:
                        pass
        except Exception:
            try:
                self.conn.rollback()
            except Exception:
                pass

        self.conn.commit()

    def data_version(self) -> int:
        return read_db_version(self.conn)

    def bump_meta(self, key: str, commit: bool = True):
        bump_meta(self.conn, key, commit=commit)

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def list_responded_models(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Modèles ayant déjà répondu (score_batch), classés par score final.

        Tableau : score benchmark global × score latence × score succès (moyenne
        des 5 fenêtres last_5/1h/1d/1w/all, chacun sqrt((1+succ)/(1+tot)))."""
        rows = self.conn.execute("""
            SELECT provider_ref, model_ref, score_etire, score_fail_rate,
                   score_latency, score_final,
                   score_last_5, score_1h, score_1d, score_1w, score_all,
                   updated_at
            FROM score_batch
            WHERE score_final IS NOT NULL
            ORDER BY score_final DESC
        """).fetchall()
        out: List[Dict[str, Any]] = []
        for r in rows:
            out.append({
                "model_ref": r["model_ref"],
                "provider_ref": r["provider_ref"],
                "score_benchmark": round(float(r["score_etire"] or 0.0), 4),
                "score_latency": round(float(r["score_latency"] or 0.0), 4),
                "score_success": round(float(r["score_fail_rate"] or 0.0), 4),
                "score_last_5": round(float(r["score_last_5"] or 0.0), 4),
                "score_1h": round(float(r["score_1h"] or 0.0), 4),
                "score_1d": round(float(r["score_1d"] or 0.0), 4),
                "score_1w": round(float(r["score_1w"] or 0.0), 4),
                "score_all": round(float(r["score_all"] or 0.0), 4),
                "score_total": round(float(r["score_final"] or 0.0), 4),
            })
        out.sort(key=lambda x: x["score_total"], reverse=True)
        return out[:limit]

    def close(self):
        self.conn.close()


# ──────────────────────────────────────────────
#  AgentsDB — Base dédiée aux agents
# ──────────────────────────────────────────────

