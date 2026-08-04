
"""Catalogue models and local SQLite repository.

This module centralizes all catalogue-related data models and queries
for the local SQLite database (``:
- SQLite WAL mode, busy timeout, foreign keys
- Autocommit semantics where applicable
- Locks handled via SQLite busy timeout
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sql.migrations import (
    _add_column_if_missing,
    _default_class_for_ref,
    _ensure_classes_outils_table,
    resolve_classe_id,
)
from services._common import mw_home


def _default_catalogue_db() -> Path:
    return mw_home() / "catalogue.db"


def _row_to_dict(row: sqlite3.Row) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(row)


def _rows_to_list(rows: List[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(r) for r in rows]


class CatalogueDB:
    """Point d'entrée pour la base catalogue (distant / synchro).

    Crée automatiquement les tables si elles n'existent pas.
    Usage:
        cat = CatalogueDB()
        cat.sync_from_url("http://localhost:8765/api")
        cat.close()
    """

    def __init__(self, db_path: Optional[Path] = None) -> None:
        self.db_path = Path(db_path) if db_path else _default_catalogue_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        schema = Path(__file__).resolve().parent.parent / "catalogue_schema.sql"
        cur = self.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='catalogue_providers'"
        )
        if not cur.fetchone():
            if schema.exists():
                self.conn.executescript(schema.read_text())
        else:
            try:
                self.conn.execute("""
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
                self.conn.rollback()
            try:
                self.conn.executescript("""
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
                self.conn.rollback()
            try:
                self.conn.execute("ALTER TABLE catalogue_outils ADD COLUMN tool_type TEXT")
            except Exception:
                self.conn.rollback()

        try:
            _ensure_classes_outils_table(self.conn)
            _add_column_if_missing(
                self.conn, "catalogue_outils", "classe_outil_id",
                "INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL",
            )
            try:
                self.conn.execute(
                    "CREATE INDEX IF NOT EXISTS idx_outils_classe "
                    "ON catalogue_outils(classe_outil_id)"
                )
            except Exception:
                self.conn.rollback()
            for row in self.conn.execute(
                "SELECT outil_id, ref FROM catalogue_outils WHERE classe_outil_id IS NULL"
            ).fetchall():
                classe_ref = _default_class_for_ref(row["ref"])
                cid = resolve_classe_id(self.conn, classe_ref)
                if cid is not None:
                    self.conn.execute(
                        "UPDATE catalogue_outils SET classe_outil_id=? WHERE outil_id=?",
                        (cid, row["outil_id"]))
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration classes_outils ignorée: {e}")

        try:
            cur = self.conn.execute("PRAGMA table_info(catalogue_aliases)").fetchall()
            cols = {row[1] for row in cur}
            if cur and "target" not in cols:
                self.conn.execute("DROP TABLE IF EXISTS catalogue_aliases")
            self.conn.execute("""
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
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_target_scope "
                "ON catalogue_aliases(target, scope)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aliases_canonical "
                "ON catalogue_aliases(canonical_ref)")
            self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration catalogue_aliases ignorée: {e}")

        try:
            pc = self.conn.execute("SELECT COUNT(*) FROM catalogue_providers").fetchone()[0]
            ec = 0
            try:
                ec = self.conn.execute("SELECT COUNT(*) FROM provider_endpoints").fetchone()[0]
            except Exception:
                ec = 0
            if (pc == 0 or ec == 0) and schema.exists():
                self.conn.executescript(schema.read_text())
                self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Seed ignoré: {e}")

        try:
            _add_column_if_missing(self.conn, "provider_endpoints", "local_latency", "REAL")
            _add_column_if_missing(self.conn, "provider_endpoints", "global_quality", "REAL")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration provider_endpoints ignorée: {e}")

        try:
            _add_column_if_missing(self.conn, "provider_models", "context_window_effective", "INTEGER")
            _add_column_if_missing(self.conn, "provider_models", "available", "INTEGER DEFAULT 1")
            _add_column_if_missing(self.conn, "provider_models", "free_tier", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "cost_per_thinking_token", "TEXT")
            _add_column_if_missing(self.conn, "provider_models", "unavailable", "INTEGER DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "noretryuntil", "REAL DEFAULT 0")
            _add_column_if_missing(self.conn, "provider_models", "notrytime", "REAL DEFAULT 0")
            self.conn.execute("""
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
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_audit_provider_model "
                "ON context_audit_log(provider_ref, model_ref)")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration context_audit_log ignorée: {e}")

        try:
            self.conn.execute("""
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
            _add_column_if_missing(self.conn, "model_call_log", "agent_id", "TEXT")
            _add_column_if_missing(self.conn, "model_call_log", "error_msg", "TEXT")
            _add_column_if_missing(self.conn, "model_call_log", "call_type", "TEXT DEFAULT 'chat'")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_provider_model "
                "ON model_call_log(provider_id, model_id, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_agent "
                "ON model_call_log(agent_id, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_call_log_created "
                "ON model_call_log(created_at, id)")
            self.conn.execute("""
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
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_created "
                "ON model_call_log_archive(created_at, id)")
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_archive_model "
                "ON model_call_log_archive(provider_id, model_id, id)")
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Migration model_call_log ignorée: {e}")

        try:
            self.conn.execute("""
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
            self.conn.execute("""
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
            self.conn.execute("""
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
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS budget_tags (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    code TEXT NOT NULL UNIQUE, label TEXT NOT NULL,
                    unit TEXT, scope TEXT
                )
            """)
            self.conn.execute("""
                INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
                    ('req_per_min','Requetes / minute','requests','requests'),
                    ('req_per_day','Requetes / jour','requests','requests'),
                    ('tok_per_min','Tokens / minute','tokens','tokens'),
                    ('tok_per_hour','Tokens / heure','tokens','tokens'),
                    ('tok_per_day','Tokens / jour','tokens','tokens'),
                    ('cost_per_day','Cout / jour (USD)','usd','cost'),
                    ('cost_per_month','Cout / mois (USD)','usd','cost')
            """)
            self.conn.execute("""
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
            self.conn.execute("""
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
            self.conn.rollback()
            print(f"⚠️  Migration tables d'acces ignoree: {e}", file=_sys.stderr)

        try:
            mc = self.conn.execute("SELECT COUNT(*) FROM catalogue_models").fetchone()[0]
            pmc = 0
            try:
                pmc = self.conn.execute("SELECT COUNT(*) FROM provider_models").fetchone()[0]
            except Exception:
                pmc = 0
            if mc == 0 or pmc == 0:
                from modules.llm_manager.llm_manager import seed_models, seed_provider_models
                if mc == 0:
                    seed_models(self)
                    self.conn.commit()
                if pmc == 0:
                    seed_provider_models(self)
                    self.conn.commit()
        except Exception as e:
            self.conn.rollback()
            print(f"⚠️  Seed modèles ignoré: {e}")
        try:
            self.conn.commit()
        except Exception:
            self.conn.rollback()

    @contextmanager
    def transaction(self):  # type: ignore[override]
        try:
            yield
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def add_alias(self, source: str, target: str, scope: str, alias: str,
                  canonical_ref: str, priority: int = 0) -> Optional[int]:
        if alias == canonical_ref:
            return None
        cur = self.conn.execute("""
            INSERT INTO catalogue_aliases (source, target, scope, alias, canonical_ref, priority)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, target, scope, alias) DO UPDATE SET
                canonical_ref=excluded.canonical_ref,
                priority=excluded.priority
        """, (source, target, scope, alias, canonical_ref, priority))
        rowid = cur.lastrowid
        cur.fetchall()
        self.conn.commit()
        return rowid

    def resolve_alias(self, target: str, scope: str, name: str) -> str:
        row = self.conn.execute("""
            SELECT canonical_ref FROM catalogue_aliases
            WHERE target=? AND scope=? AND alias=?
            ORDER BY priority DESC, id ASC LIMIT 1
        """, (target, scope, name)).fetchone()
        return row["canonical_ref"] if row else name

    def alias_map(self, target: str, scope: str) -> Dict[str, str]:
        rows = self.conn.execute("""
            SELECT alias, canonical_ref, priority, id FROM catalogue_aliases
            WHERE target=? AND scope=?
        """, (target, scope)).fetchall()
        out: Dict[str, str] = {}
        best: Dict[str, tuple] = {}
        for r in rows:
            key = r["alias"]
            score = (r["priority"], -r["id"])
            if key not in best or score > best[key]:
                best[key] = score
                out[key] = r["canonical_ref"]
        return out

    def list_aliases(self, source: Optional[str] = None,
                     target: Optional[str] = None,
                     scope: Optional[str] = None) -> List[Dict[str, Any]]:
        clauses = []
        wargs: List[Any] = []
        if source:
            clauses.append("source=?")
            wargs.append(source)
        if target:
            clauses.append("target=?")
            wargs.append(target)
        if scope:
            clauses.append("scope=?")
            wargs.append(scope)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM catalogue_aliases{where} ORDER BY target, scope, alias",
            wargs
        ).fetchall()
        return _rows_to_list(rows)

    def sync_providers(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_providers
                    (ref, name, provider_type, api_type, website, is_free_tier_provider)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("provider_type"), r.get("api_type"),
                  r.get("website"), r.get("is_free_tier_provider", 0)))
            count += 1
        return count

    def sync_models(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            ref = r.get("ref") or r.get("id")
            if not ref:
                continue
            name = r.get("name") or ref
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_models
                    (ref, name, developer, release_year, architecture, parameter_count,
                     modality, target_use, license, is_open_weights, parent_model_ref)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (ref, name, r.get("developer"), r.get("release_year"),
                  r.get("architecture"), r.get("parameter_count"),
                  r.get("target_use"), r.get("license"), r.get("is_open_weights", 0),
                  r.get("parent_model_ref")))
            count += 1
        return count

    def sync_tools(self, rows: List[Dict]) -> int:
        count = 0
        _ensure_classes_outils_table(self.conn)
        for r in rows:
            classe_ref = r.get("classe") or r.get("classe_ref") or _default_class_for_ref(r.get("ref", ""))
            classe_id = resolve_classe_id(self.conn, classe_ref)
            self.conn.execute("""
                INSERT INTO catalogue_outils (ref, nom, description, tool_type, classe_outil_id)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(ref) DO UPDATE SET
                    nom=excluded.nom, description=excluded.description,
                    tool_type=COALESCE(excluded.tool_type, catalogue_outils.tool_type),
                    classe_outil_id=COALESCE(excluded.classe_outil_id, catalogue_outils.classe_outil_id)
            """, (r["ref"], r["name"], r.get("description"), r.get("tool_type"), classe_id))
            ver = r.get("current_version")
            if ver:
                oid = self.conn.execute(
                    "SELECT outil_id FROM catalogue_outils WHERE ref=?", (r["ref"],)
                ).fetchone()[0]
                self.conn.execute("""
                    INSERT INTO catalogue_versions (outil_id, nom_version, description)
                    VALUES (?, ?, ?)
                    ON CONFLICT(outil_id, nom_version) DO NOTHING
                """, (oid, ver, r.get("description")))
            count += 1
        return count

    def sync_commands(self, rows: List[Dict]) -> int:
        count = 0
        for r in rows:
            self.conn.execute("""
                INSERT OR REPLACE INTO catalogue_commands
                    (ref, name, description, command_type)
                VALUES (?, ?, ?, ?)
            """, (r["ref"], r["name"], r.get("description"), r.get("command_type")))
            count += 1
        return count

    def sync_from_url(self, url: str) -> Dict[str, int]:
        import urllib.request
        import json

        endpoints = {
            "providers": self.sync_providers,
            "models": self.sync_models,
            "tools": self.sync_tools,
            "commands": self.sync_commands,
        }
        results = {}

        for name, sync_fn in endpoints.items():
            endpoint = f"{url.rstrip('/')}/{name}"
            try:
                req = urllib.request.Request(endpoint, headers={"User-Agent": "ModelWeaver/1.0"})
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                    if isinstance(data, list):
                        results[name] = sync_fn(data)
                    else:
                        print(f"  ⚠️  {name}: format inattendu")
                        results[name] = 0
                print(f"  ✅ {name}: {results[name]} entrées synchronisées")
            except Exception as e:
                print(f"  ⚠️  {name}: échec synchro ({e})")
                results[name] = -1

        self.conn.commit()
        return results

    def fetch_model_scores(self) -> Dict[str, int]:
        rows = self.conn.execute("""
            SELECT me.model_ref, me.global_score, me.score_quality,
                   me.score_chat, me.score_coding, me.score_reasoning,
                   me.score_knowledge, me.score_agentic,
                   me.is_synthetic, me.samples, me.source_count,
                   pm.provider_id, pm.id AS pm_id,
                   pm.cost_per_input_token, pm.cost_per_output_token
            FROM model_efficacy me
            JOIN catalogue_models cm ON cm.id = me.model_id
            JOIN provider_models pm ON pm.model_id = cm.id
            WHERE me.is_synthetic = 0 OR me.source_count > 0
        """).fetchall()

        count = 0
        for r in rows:
            try:
                cost_in = 0.0
                cost_out = 0.0
                try:
                    cost_in = float(r["cost_per_input_token"] or 0)
                except (ValueError, TypeError):
                    pass
                try:
                    cost_out = float(r["cost_per_output_token"] or 0)
                except (ValueError, TypeError):
                    pass

                self.conn.execute("""
                    INSERT OR REPLACE INTO model_provider_scoring
                    (provider_id, model_id, endpoint_id,
                     quality_score, score_chat, score_coding, score_reasoning,
                     score_knowledge, score_agentic, global_score,
                     is_synthetic, cost_per_1k_input, cost_per_1k_output,
                     benchmark_ref, confidence, last_scored_at, updated_at)
                    VALUES (?, ?, NULL,
                            ?, ?, ?, ?, ?, ?, ?,
                            ?, ?, ?, ?, ?,
                            strftime('%s','now'), strftime('%s','now'))
                """, (
                    r["provider_id"],
                    r["model_id"],
                    r["score_quality"], r["score_chat"], r["score_coding"],
                    r["score_reasoning"], r["score_knowledge"], r["score_agentic"],
                    r["global_score"],
                    r["is_synthetic"],
                    cost_in * 1000, cost_out * 1000,
                    r["model_ref"],
                    0.6 if r["is_synthetic"] else 1.0,
                ))
                count += 1
            except Exception as e:
                print(f"  ⚠ skip scoring {r.get('model_ref','?')}: {e}")

        self.conn.commit()
        return {"model_provider_scoring": count}

    def bump_popularity(self, ref: str, action: str) -> None:
        row = self.conn.execute(
            "SELECT outil_id FROM catalogue_outils WHERE ref=?", (ref,)).fetchone()
        if not row:
            return
        oid = row["outil_id"]
        self.conn.execute("INSERT OR IGNORE INTO outils_popularite (outil_id) VALUES (?)", (oid,))
        if action == "install":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_install=nb_install+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET install_count=install_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        elif action == "uninstall":
            self.conn.execute(
                "UPDATE outils_popularite SET nb_desinstall=nb_desinstall+1, updated_at=strftime('%s','now') WHERE outil_id=?",
                (oid,))
            self.conn.execute(
                """UPDATE catalogue_recettes SET uninstall_count=uninstall_count+1, updated_at=strftime('%s','now')
                   WHERE version_id=(SELECT version_id FROM catalogue_versions WHERE outil_id=?)
                     AND manager='pip' AND enabled=1""", (oid,))
        self.conn.commit()

    def get_catalogue_tools(self, os_key: str = "linux", arch_key: str = "x86_64") -> dict:
        cur = self.conn.execute("""
            SELECT DISTINCT o.ref, o.nom, o.description, o.tool_type,
                   c.ref AS classe_ref, c.nom AS classe_nom,
                   r.manager, r.package, r.os, r.arch, r.confidence
            FROM catalogue_outils o
            LEFT JOIN classes_outils c ON c.classe_id = o.classe_outil_id
            JOIN catalogue_versions v ON v.outil_id = o.outil_id
            JOIN catalogue_recettes r ON r.version_id = v.version_id
            WHERE r.os IN (?, 'all') AND r.arch IN (?, 'all') AND r.enabled = 1
            ORDER BY o.nom, r.manager
        """, (os_key, arch_key))
        cols = [d[0] for d in cur.description]
        tools_map = {}
        for row in cur.fetchall():
            d = dict(zip(cols, row))
            ref = d["ref"]
            if ref not in tools_map:
                classe_ref = d["classe_ref"] or _default_class_for_ref(ref)
                classe_nom = d["classe_nom"] or classe_ref
                tools_map[ref] = {
                    "ref": ref, "name": d["nom"],
                    "description": d["description"],
                    "tool_type": d["tool_type"],
                    "classe_ref": classe_ref,
                    "classe": classe_nom,
                    "managers": [],
                }
            tools_map[ref]["managers"].append({
                "manager": d["manager"], "package": d["package"],
                "os": d["os"], "arch": d["arch"], "confidence": d["confidence"],
            })
        tools = list(tools_map.values())
        return {"tools": tools, "count": len(tools)}

    def close(self) -> None:
        self.conn.close()
