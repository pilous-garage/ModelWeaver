"""Conversations de chat (dev-chat) — skills pour l'agent pilote.

Un pilote (agent chat) a des conversations (table conversations + messages).
Ces skills permettent au LLM pilote de :
  - lister ses conversations (conversation/list)
  - renommer la conversation courante (conversation/rename)
  - changer le workspace de la conversation (conversation/switch_workspace)
  - lire les messages / le contexte texte (conversation/context)

La conversation courante est passée en input (conversation_id) — injectée par
le workflow dev-chat depuis la session active.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _agent_id_from_home(home: str, inputs: dict) -> str:
    """agent_id depuis le home (agent_home/{id}/…) ou l'input."""
    aid = inputs.get("agent_id") or inputs.get("from_agent_id") or ""
    if aid:
        return str(aid)
    parts = home.split("agent_home")
    if len(parts) > 1:
        seg = parts[1].strip("/").split("/")[0]
        if seg.isdigit():
            return seg
    return ""


def _repo():
    from modules.sql.agents_repo import ConversationRepository
    from modules.sql.db import AgentsDB
    db = AgentsDB()
    return ConversationRepository(db.conn), db


def list_conversations(inputs: dict, home: str) -> dict:
    """Liste les conversations du pilote (agent_id du home)."""
    agent_id = _agent_id_from_home(home, inputs)
    if not agent_id:
        return {"ok": False, "error": "agent_id introuvable depuis le home"}
    repo, db = _repo()
    try:
        convs = repo.list_for_agent(int(agent_id))
        # Ajouter le nombre de messages + le dernier message pour l'aperçu.
        out = []
        for c in convs:
            msgs = repo.list_messages(c["id"], limit=1)
            out.append({**c, "message_count":
                        len(repo.list_messages(c["id"])),
                        "last_message": msgs[0] if msgs else None})
        return {"ok": True, "conversations": out, "count": len(out)}
    finally:
        db.close()


def get_conversation(inputs: dict, home: str) -> dict:
    """Retourne une conversation + ses messages (pour reprise de contexte)."""
    conv_id = inputs.get("conversation_id")
    if not conv_id:
        return {"ok": False, "error": "conversation_id requis"}
    repo, db = _repo()
    try:
        conv = repo.get(int(conv_id))
        if not conv:
            return {"ok": False, "error": "conversation introuvable"}
        messages = repo.list_messages(int(conv_id))
        return {"ok": True, "conversation": conv, "messages": messages}
    finally:
        db.close()


def rename_conversation(inputs: dict, home: str) -> dict:
    """Renomme une conversation (nom proposé par le LLM).

    Le gestionnaire d'auth peut demander une authorisation pour renommer une
    conversation partagée (conversation_id scoped).
    """
    conv_id = inputs.get("conversation_id")
    name = (inputs.get("name") or "").strip()
    if not conv_id:
        return {"ok": False, "error": "conversation_id requis"}
    if not name:
        return {"ok": False, "error": "name requis"}
    if len(name) > 120:
        name = name[:120]
    repo, db = _repo()
    try:
        ok = repo.rename(int(conv_id), name)
        return {"ok": ok, "conversation_id": int(conv_id), "name": name}
    finally:
        db.close()


def switch_workspace(inputs: dict, home: str) -> dict:
    """Change le workspace de la conversation.

    Peut nécessiter une authorisation (changer de projet = nouvelle portée).
    L'agent pilote doit appeler comm/ask_authorisation AVANT si besoin.
    """
    conv_id = inputs.get("conversation_id")
    workspace_id = (inputs.get("workspace_id") or "").strip()
    if not conv_id:
        return {"ok": False, "error": "conversation_id requis"}
    if not workspace_id:
        return {"ok": False, "error": "workspace_id requis"}
    repo, db = _repo()
    try:
        ok = repo.set_workspace(int(conv_id), workspace_id)
        return {"ok": ok, "conversation_id": int(conv_id),
                "workspace_id": workspace_id}
    finally:
        db.close()


def conversation_context(inputs: dict, home: str) -> dict:
    """Contexte texte unique (10k chars) pour re-injection au pilote.

    Format : utilisateur: "…" / llm (model): "…" — ignore thinking/tools/err.
    """
    conv_id = inputs.get("conversation_id")
    max_chars = int(inputs.get("max_chars", 10000))
    if not conv_id:
        return {"ok": False, "error": "conversation_id requis"}
    repo, db = _repo()
    try:
        ctx = repo.text_context(int(conv_id), max_chars=max_chars)
        return {"ok": True, "context": ctx, "chars": len(ctx)}
    finally:
        db.close()


__skills__ = ["list_conversations", "get_conversation", "rename_conversation",
              "switch_workspace", "conversation_context"]
