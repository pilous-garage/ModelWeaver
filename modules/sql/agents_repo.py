#!/usr/bin/env python3
"""Agents — DB des agents + WaitForRepository.

Extrait de modules/sql/db.py (issue #11, découpage par domaine).
Contient : WaitForRepository, AgentsDB.
"""

import json
import sqlite3
import time
import uuid
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from modules.sql.schema import (
    _default_agents_db, _rows_to_list, _add_column_if_missing, read_meta,
)
from services._common import mw_home

# Agent « humain » = MAX_UINT64 (2^64 - 1). Un agent maître n'a pas de
# propriétaire (NULL) ; un sub-agent a id_proprietaire = son agent maître
# (ou MAX_UINT64 si la chaîne remonte jusqu'à l'humain). Stocké en TEXTE
# (décimal) car 2^64-1 dépasse l'INTEGER signé 64 bits de SQLite.
HUMAN_AGENT_ID = str((1 << 64) - 1)


class WaitForRepository:
    """Agents endormis en attente d'une condition (wait_for).

    Chaque ligne = un agent qui attend une condition (JSON) : type de travail
    dispo (issue_open, task_for_role...). Le waker liste les 'waiting' dont la
    condition est remplie et les réveille — FIFO (les plus anciens d'abord) et
    un à la fois par condition (évite de réveiller tous les agents d'un coup).
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    def register(self, agent_id: int, condition: dict,
                 expires_at: Optional[str] = None) -> int:
        """Enregistre un agent en attente d'une condition (status waiting).

        UN SEUL wait_for actif par agent : les précédents (waiting/ready) sont
        marqués 'done' avant d'insérer — sinon l'agent s'empile à chaque
        re-endormissement (des centaines de lignes observées).
        """
        self.conn.execute(
            "UPDATE wait_for SET status = 'done' WHERE agent_id = ? "
            "AND status IN ('waiting','ready')", (agent_id,))
        cur = self.conn.execute(
            "INSERT INTO wait_for (agent_id, condition, status, expires_at) "
            "VALUES (?, ?, 'waiting', ?)",
            (agent_id, json.dumps(condition), expires_at))
        self.conn.commit()
        return cur.lastrowid

    def waiting(self) -> List[Dict[str, Any]]:
        """Agents en attente, triés FIFO (les plus anciens d'abord)."""
        cur = self.conn.execute(
            "SELECT * FROM wait_for WHERE status = 'waiting' "
            "ORDER BY created_at ASC, id ASC")
        return _rows_to_list(cur.fetchall())

    def ready(self, agent_id: int, condition: dict) -> bool:
        """Vrai si l'agent attend et que sa condition est remplie (à surcharger
        par le waker via un prédicat). Ici : simple existence d'attente."""
        return True

    def mark_ready(self, wait_id: int) -> None:
        self.conn.execute(
            "UPDATE wait_for SET status = 'ready', ready_at = datetime('now') "
            "WHERE id = ?", (wait_id,))
        self.conn.commit()

    def mark_done(self, agent_id: int) -> None:
        self.conn.execute(
            "UPDATE wait_for SET status = 'done' WHERE agent_id = ? "
            "AND status IN ('waiting','ready')", (agent_id,))
        self.conn.commit()

    def remove_expired(self) -> int:
        cur = self.conn.execute(
            "DELETE FROM wait_for WHERE status != 'done' "
            "AND expires_at IS NOT NULL AND expires_at < datetime('now')")
        self.conn.commit()
        return cur.rowcount


class AgentsDB:
    """Point d'entrée pour la base agents (domaine distinct).

    Banque séparée de modelweaver.db — contient l'identité, le runtime,
    les métriques et les signaux des agents.

    Usage:
        db = AgentsDB()
        db.conn.execute("SELECT * FROM agents")
        db.close()
    """

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = Path(db_path) if db_path else _default_agents_db()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # Autocommit : les agents tournent en threads dans agent-manager et
        # partagent cette connexion. Sans autocommit, une transaction implicite
        # laissée par un thread bloque/imbrique les écritures des autres
        # ("cannot start a transaction within a transaction", "database is
        # locked"). Chaque execute est immédiat.
        self.conn = sqlite3.connect(str(self.db_path), check_same_thread=False,
                                    isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON")
        self.conn.execute("PRAGMA journal_mode = WAL")
        self.conn.execute("PRAGMA busy_timeout = 5000")
        self._ensure_schema()
        self.wait_for = WaitForRepository(self.conn)

    def _ensure_schema(self):
        schema = Path(__file__).resolve().parent / "agents_schema.sql"
        if schema.exists():
            self.conn.executescript(schema.read_text())
        # Migration V0.6.8 : storage_json (espace disque proprio par agent)
        _add_column_if_missing(self.conn, "agents", "storage_json", "TEXT")
        # Migration V0.9.x : id_proprietaire (uint64 décimal, agent maître ou
        # MAX_UINT64=humain) + id_team (team_id stable) — sub-agents.
        _add_column_if_missing(self.conn, "agents", "id_proprietaire", "TEXT")
        _add_column_if_missing(self.conn, "agents", "id_team", "INTEGER")
        _add_column_if_missing(self.conn, "agent_runtime", "id_proprietaire", "TEXT")
        _add_column_if_missing(self.conn, "agent_runtime", "id_team", "INTEGER")
        # Migration V0.8.5 : nouveaux types de signaux (wakeup, sleep)
        _add_column_if_missing(self.conn, "agent_signals", "source_agent_id", "INTEGER")
        # Migration V0.8.9 : wait_for — agents endormis en attente d'une
        # condition (tâche/issue dispo). Le waker les réveille quand la
        # condition est remplie (un à la fois).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS wait_for (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id    INTEGER NOT NULL,
                condition   TEXT NOT NULL,   -- JSON {type, workspace_id, role}
                status      TEXT DEFAULT 'waiting',  -- waiting / ready / done
                created_at  TEXT NOT NULL DEFAULT (datetime('now')),
                ready_at    TEXT,
                expires_at  TEXT
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_wait_for_status "
            "ON wait_for(status, agent_id)")
        # Autorisations : demandes d'autorisation persistées (membres→leader→
        # humain). Le request_handler (en mémoire) écrit ici pour que le GUI
        # puisse lister/gérer les demandes. Tous les statuts sont conservés.
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS auth_requests (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                request_id      TEXT NOT NULL UNIQUE,
                agent_id        TEXT NOT NULL,
                team_id         TEXT,
                action          TEXT NOT NULL,   -- path_read | path_write | command
                target          TEXT,            -- JSON {path|command}
                reason          TEXT DEFAULT '',
                scope           TEXT DEFAULT 'once',
                approver_level  TEXT DEFAULT 'leader',  -- leader | human
                request_type    TEXT DEFAULT 'pending_leader',
                status          TEXT DEFAULT 'pending',
                approver_id     TEXT,
                rejection_reason TEXT,
                resolved_scope  TEXT,
                created_at      REAL,
                resolved_at     REAL
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_status "
            "ON auth_requests(status, approver_level)")
        # Autorisation SCOPÉE à une conversation (un agent chat peut avoir
        # plusieurs conversations ; on autorise une CONVERSATION, pas l'agent).
        _add_column_if_missing(self.conn, "auth_requests", "conversation_id",
                               "TEXT")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_auth_conv "
            "ON auth_requests(conversation_id, status)")

        # ── Conversations de chat (dev-chat) ──
        # Une conversation = un agent pilote (role_type='chat') qui discute.
        # Un agent peut avoir PLUSIEURS conversations (menu déroulant). Le nom
        # est renommable (toolcall conversation/rename), le workspace affiché
        # et changeable (conversation/switch_workspace).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                agent_id     INTEGER NOT NULL,
                name         TEXT NOT NULL,
                workspace_id TEXT,
                created_at   INTEGER DEFAULT (strftime('%s','now')),
                updated_at   INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_agent "
            "ON conversations(agent_id, updated_at)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_conv_ws "
            "ON conversations(workspace_id)")

        # ── Messages de conversation (journal des échanges) ──
        # Chaque événement du chat : envoi humain, réponse texte, thinking,
        # tool_call, tool_result, branchement/fallback LLM, erreur. Une ligne
        # = un événement atomique (time_start==time_finish pour les instants).
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_messages (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id  INTEGER NOT NULL,
                time_start       REAL NOT NULL,
                time_finish      REAL,
                type             TEXT NOT NULL,
                payload_json     TEXT,
                seq              INTEGER DEFAULT 0,
                updated_at       INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_convmsg_conv "
            "ON conversation_messages(conversation_id, seq)")
        self.conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_convmsg_type "
            "ON conversation_messages(conversation_id, type)")

    def read_meta(self, key: str, default: int = 0) -> int:
        return read_meta(self.conn, key, default=default)

    def bump_meta(self, key: str) -> None:
        bump_meta(self.conn, key, commit=False)

    def close(self):
        self.conn.close()


class ConversationRepository:
    """Conversations de chat (dev-chat) : CRUD + journal des messages.

    Une conversation appartient à UN agent pilote (role_type='chat'), mais un
    agent peut en avoir plusieurs (menu déroulant GUI). Les messages sont des
    événements atomiques (type : human_message, llm_text, thinking, tool_call,
    tool_result, llm_attach, llm_fallback, llm_reattach, error).
    """

    # Types d'événements de conversation.
    T_HUMAN = "human_message"
    T_LLM_TEXT = "llm_text"
    T_THINKING = "thinking"
    T_TOOL_CALL = "tool_call"
    T_TOOL_RESULT = "tool_result"
    T_LLM_ATTACH = "llm_attach"      # branchement initial d'un LLM
    T_LLM_FALLBACK = "llm_fallback"  # bascule (rate_limit, erreur)
    T_LLM_REATTACH = "llm_reattach"   # retour au modèle sélectionné
    T_ERROR = "error"

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # ── Conversations ──

    def create(self, agent_id: int, name: str = "",
               workspace_id: Optional[str] = None) -> int:
        name = name or f"conversation_{int(time.time())}"
        cur = self.conn.execute(
            "INSERT INTO conversations (agent_id, name, workspace_id) "
            "VALUES (?, ?, ?)", (agent_id, name, workspace_id))
        self.conn.commit()
        return cur.lastrowid

    def list_for_agent(self, agent_id: int) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM conversations WHERE agent_id = ? "
            "ORDER BY updated_at DESC", (agent_id,)).fetchall()
        return [dict(r) for r in rows]

    def list_all(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]

    def get(self, conv_id: int) -> Optional[Dict[str, Any]]:
        r = self.conn.execute(
            "SELECT * FROM conversations WHERE id = ?", (conv_id,)).fetchone()
        return dict(r) if r else None

    def rename(self, conv_id: int, name: str) -> bool:
        name = (name or "").strip()[:50]  # limite 50 chars
        if not name:
            return False
        cur = self.conn.execute(
            "UPDATE conversations SET name = ?, updated_at = "
            "strftime('%s','now') WHERE id = ?", (name, conv_id))
        self.conn.commit()
        return cur.rowcount > 0

    def set_workspace(self, conv_id: int, workspace_id: str) -> bool:
        cur = self.conn.execute(
            "UPDATE conversations SET workspace_id = ?, updated_at = "
            "strftime('%s','now') WHERE id = ?", (workspace_id, conv_id))
        self.conn.commit()
        return cur.rowcount > 0

    def delete(self, conv_id: int) -> bool:
        self.conn.execute(
            "DELETE FROM conversation_messages WHERE conversation_id = ?",
            (conv_id,))
        cur = self.conn.execute(
            "DELETE FROM conversations WHERE id = ?", (conv_id,))
        self.conn.commit()
        return cur.rowcount > 0

    def touch(self, conv_id: int) -> None:
        self.conn.execute(
            "UPDATE conversations SET updated_at = strftime('%s','now') "
            "WHERE id = ?", (conv_id,))
        self.conn.commit()

    # ── Messages ──

    def append(self, conversation_id: int, msg_type: str,
               time_start: float, time_finish: Optional[float] = None,
               payload: Optional[Dict[str, Any]] = None) -> int:
        seq = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) + 1 FROM conversation_messages "
            "WHERE conversation_id = ?", (conversation_id,)).fetchone()[0]
        cur = self.conn.execute(
            "INSERT INTO conversation_messages "
            "(conversation_id, time_start, time_finish, type, payload_json, seq) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (conversation_id, time_start, time_finish, msg_type,
             json.dumps(payload or {}), seq))
        self.conn.commit()
        self.touch(conversation_id)
        return cur.lastrowid

    def list_messages(self, conversation_id: int,
                      limit: Optional[int] = None) -> List[Dict[str, Any]]:
        sql = ("SELECT * FROM conversation_messages WHERE conversation_id = ? "
               "ORDER BY seq")
        args: tuple = (conversation_id,)
        if limit:
            # dernières `limit` lignes (mais ordre ascendant dans la sortie)
            sql = ("SELECT * FROM ("
                   "  SELECT * FROM conversation_messages "
                   "  WHERE conversation_id = ? ORDER BY seq DESC LIMIT ?"
                   ") ORDER BY seq")
            args = (conversation_id, limit)
        rows = self.conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def count_text_exchanges(self, conversation_id: int) -> int:
        """Nombre d'échanges TEXTE (human_message + llm_text) d'une conversation."""
        r = self.conn.execute(
            "SELECT COUNT(*) c FROM conversation_messages "
            "WHERE conversation_id = ? AND type IN (?, ?)",
            (conversation_id, self.T_HUMAN, self.T_LLM_TEXT)).fetchone()
        return r[0] if r else 0

    def text_context(self, conversation_id: int,
                     max_chars: int = 10000) -> str:
        """Contexte texte unique (utilisateur/LLM) pour re-injection au pilote.

        Ne garde que les échanges de TEXTE (human_message + llm_text), ignore
        thinking/tool_call/erreurs/fallback. Format :
            utilisateur: "xxx"
            llm (provider/model): "yyy"
        Limité aux `max_chars` derniers caractères. Un seul string, pas de JSON.
        """
        rows = self.conn.execute(
            "SELECT type, payload_json, time_start FROM conversation_messages "
            "WHERE conversation_id = ? AND type IN (?, ?) ORDER BY seq",
            (conversation_id, self.T_HUMAN, self.T_LLM_TEXT)).fetchall()
        lines = []
        for r in rows:
            p = json.loads(r["payload_json"] or "{}")
            text = (p.get("text") or p.get("content") or "").strip()
            if not text:
                continue
            if r["type"] == self.T_HUMAN:
                lines.append(f'utilisateur: "{text}"')
            else:
                model = p.get("model") or p.get("model_ref") or "?"
                lines.append(f'llm ({model}): "{text}"')
        if not lines:
            return ""
        # Construire depuis la fin pour garder les N derniers caractères.
        out = ""
        for line in reversed(lines):
            if len(out) + len(line) + 1 > max_chars:
                break
            out = line + "\n" + out
        return out.rstrip()


# ──────────────────────────────────────────────
#  Quick test
# ──────────────────────────────────────────────

if __name__ == "__main__":
    db = ModelWeaverDB()
    print(f"🔌 Connecté à {db.db_path}")

    print(f"\n  Providers : {len(db.providers.list_all())}")
    print(f"  Modèles   : {len(db.models.list_all())}")
    print(f"  Clés      : {len(db.keys.list_all())}")
    print(f"  LLMs locaux: {len(db.llms.list_all())}")
    print(f"  Commandes : {len(db.commands.list_all())}")

    g = db.providers.get("groq")
    print(f"\n  groq → {g}")

    print("\n  Recherche 'gemini':")
    for m in db.models.search("gemini", modality="text"):
        print(f"    → {m['ref']} ({m['developer']})")

    db.close()

    cat = CatalogueDB()
    print(f"\n🔌 Catalogue: {cat.db_path}")
    cat.close()
