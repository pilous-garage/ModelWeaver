#!/usr/bin/env python3
"""Chat Service — Sessions de chat concurrentes au-dessus du framework Agent.

Chaque session de chat est un **agent** (role_type='chat', occupation=
'noncontinue') : on réutilise l'identité persistante (agents.db), le cycle
hydrate/dehydrate et le AgentManager (liste/signaux). L'historique complet
de la conversation vit dans `variables_json.messages` ; une session n'écrit
QUE dans sa propre histoire, mais peut (si autorisée) LIRE l'historique des
autres sessions.

Le LLM est appelé via `LiteLLMBridge` (chat / chat_stream). Le streaming est
diffusé sur le `StreamBus` global (clé = agent_id), consultable via
`chat/session/stream` (ou `agent/stream`).
"""
import json
from typing import Any, Dict, List, Optional

from modules.sql.db import AgentsDB
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from AgentFrameWork.stream_bus import stream_bus


class ChatService:
    """Gestionnaire de sessions de chat (une session = un agent)."""

    def __init__(self, db: Optional[AgentsDB] = None, bridge: Optional[LiteLLMBridge] = None):
        self.db = db or AgentsDB()
        self._bridge = bridge or LiteLLMBridge()

    # ── helpers ──

    def _get(self, name: str) -> Optional[Dict[str, Any]]:
        row = self.db.conn.execute(
            "SELECT * FROM agents WHERE name = ? AND role_type = 'chat'", (name,)
        ).fetchone()
        return dict(row) if row else None

    def _vars(self, row: Dict[str, Any]) -> Dict[str, Any]:
        try:
            return json.loads(row["variables_json"] or "{}")
        except (json.JSONDecodeError, TypeError, KeyError):
            return {}

    def _set_vars(self, agent_id: int, variables: Dict[str, Any]) -> None:
        self.db.conn.execute(
            "UPDATE agents SET variables_json = ?, last_active_at = datetime('now') WHERE agent_id = ?",
            (json.dumps(variables), agent_id))
        self.db.conn.commit()

    # ── CRUD sessions ──

    def create_session(self, name: str, role: str = "chat",
                       provider_ref: str = "", model_ref: str = "",
                       system_prompt: str = "", allow_read_others: bool = False,
                       resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        if self._get(name):
            return {"status": "error", "error": f"session '{name}' existe déjà"}
        variables = {
            "messages": [],
            "system_prompt": system_prompt,
            "provider_ref": provider_ref,
            "model_ref": model_ref,
            "allow_read_others": bool(allow_read_others),
        }
        res = resources if resources is not None else {"llm": True}
        res.setdefault("llm", True)
        self.db.conn.execute(
            """INSERT INTO agents (name, ref, role_type, occupation, config_json,
                                   resources_json, variables_json)
               VALUES (?, ?, 'chat', 'noncontinue', ?, ?, ?)""",
            (name, f"agent:{name}", json.dumps({"kind": "chat_session"}),
             json.dumps(res), json.dumps(variables)))
        self.db.conn.commit()
        aid = self.db.conn.execute(
            "SELECT agent_id FROM agents WHERE name = ?", (name,)).fetchone()[0]
        return {"status": "ok", "agent_id": aid, "name": name}

    def list_sessions(self) -> Dict[str, Any]:
        rows = self.db.conn.execute(
            "SELECT agent_id, name, status, variables_json, resources_json "
            "FROM agents WHERE role_type = 'chat' ORDER BY name"
        ).fetchall()
        out = []
        for r in rows:
            v = self._vars(dict(r))
            out.append({
                "agent_id": r["agent_id"],
                "name": r["name"],
                "status": r["status"],
                "provider_ref": v.get("provider_ref", ""),
                "model_ref": v.get("model_ref", ""),
                "allow_read_others": v.get("allow_read_others", False),
                "messages": len(v.get("messages", [])),
            })
        return {"status": "ok", "sessions": out, "count": len(out)}

    def get_session(self, name: str) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = self._vars(row)
        return {
            "status": "ok",
            "agent_id": row["agent_id"],
            "name": row["name"],
            "provider_ref": v.get("provider_ref", ""),
            "model_ref": v.get("model_ref", ""),
            "system_prompt": v.get("system_prompt", ""),
            "allow_read_others": v.get("allow_read_others", False),
            "messages": v.get("messages", []),
        }

    def delete_session(self, name: str) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        self.db.conn.execute("DELETE FROM agent_runtime WHERE agent_id = ?", (row["agent_id"],))
        self.db.conn.execute("DELETE FROM agents WHERE agent_id = ?", (row["agent_id"],))
        self.db.conn.commit()
        stream_bus.reset(row["agent_id"])
        return {"status": "ok", "agent_id": row["agent_id"]}

    def update_session(self, name: str, system_prompt: Optional[str] = None,
                       provider_ref: Optional[str] = None, model_ref: Optional[str] = None,
                       allow_read_others: Optional[bool] = None,
                       resources: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = self._vars(row)
        if system_prompt is not None:
            v["system_prompt"] = system_prompt
        if provider_ref is not None:
            v["provider_ref"] = provider_ref
        if model_ref is not None:
            v["model_ref"] = model_ref
        if allow_read_others is not None:
            v["allow_read_others"] = bool(allow_read_others)
        self._set_vars(row["agent_id"], v)
        if resources is not None:
            self.db.conn.execute("UPDATE agents SET resources_json = ? WHERE agent_id = ?",
                                 (json.dumps(resources), row["agent_id"]))
            self.db.conn.commit()
        return {"status": "ok", "agent_id": row["agent_id"], "name": name}

    # ── conversation ──

    def send(self, name: str, message: str, provider_ref: str = "",
             model_ref: str = "", stream: bool = False,
             temperature: float = 0.7, max_tokens: Optional[int] = None) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = self._vars(row)
        p_ref = provider_ref or v.get("provider_ref", "")
        m_ref = model_ref or v.get("model_ref", "")
        if not p_ref or not m_ref:
            return {"status": "error", "error": "provider/model non défini pour la session"}
        system_prompt = v.get("system_prompt", "")
        messages = list(v.get("messages", []))
        messages.append({"role": "user", "content": message})

        # Marque l'agent comme RUNNING (intègre AgentManager + stream).
        self.db.conn.execute(
            "UPDATE agents SET status = 'RUNNING' WHERE agent_id = ?", (row["agent_id"],))
        self.db.conn.commit()

        reply = ""
        usage = {}
        try:
            if stream:
                for chunk in self._bridge.chat_stream(
                        p_ref, m_ref, messages,
                        temperature=temperature, max_tokens=max_tokens,
                        system_prompt=system_prompt):
                    reply += chunk
                    stream_bus.publish(row["agent_id"], chunk, kind="token")
            else:
                resp = self._bridge.chat(
                    p_ref, m_ref, messages,
                    temperature=temperature, max_tokens=max_tokens,
                    system_prompt=system_prompt)
                reply = resp.content
                usage = resp.usage or {}
            messages.append({"role": "assistant", "content": reply})
            v["messages"] = messages
            self._set_vars(row["agent_id"], v)
            return {
                "status": "ok",
                "agent_id": row["agent_id"],
                "name": name,
                "reply": reply,
                "model": f"{p_ref}/{m_ref}",
                "usage": usage,
                "messages": len(messages),
            }
        except Exception as e:  # LLM error -> on restore le statut, pas d'historique pollué
            self.db.conn.execute(
                "UPDATE agents SET status = 'INIT' WHERE agent_id = ?", (row["agent_id"],))
            self.db.conn.commit()
            return {"status": "error", "error": str(e), "category": "llm"}
        finally:
            self.db.conn.execute(
                "UPDATE agents SET status = 'INIT', last_active_at = datetime('now') WHERE agent_id = ?",
                (row["agent_id"],))
            self.db.conn.commit()

    def history(self, name: str) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        v = self._vars(row)
        return {"status": "ok", "name": name, "messages": v.get("messages", [])}

    def read_session(self, name: str, other: str) -> Dict[str, Any]:
        """Lit l'historique d'une AUTRE session (si celle-ci l'autorise)."""
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session lectrice introuvable"}
        v = self._vars(row)
        if not v.get("allow_read_others", False):
            return {"status": "error", "error": "cette session n'a pas le droit de lire les autres"}
        other_row = self._get(other)
        if not other_row:
            return {"status": "error", "error": "session cible introuvable"}
        ov = self._vars(other_row)
        return {"status": "ok", "reader": name, "source": other,
                "messages": ov.get("messages", [])}

    def stream(self, name: str, seq: int = 0) -> Dict[str, Any]:
        row = self._get(name)
        if not row:
            return {"status": "error", "error": "session introuvable"}
        chunks = stream_bus.since(row["agent_id"], seq)
        last = chunks[-1]["seq"] if chunks else seq
        return {"status": "ok", "agent_id": row["agent_id"], "name": name,
                "seq": last, "chunks": chunks, "count": len(chunks)}
