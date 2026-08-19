"""write — écritures du domaine dialogue_agent.

SECTIONS (convention des write.py de domaine) :
  1. WRITE NORMAL SANS TOKEN — écritures courantes (domaine OUVERT).
  2. WRITE TOKEN WRITER DÉDIÉ — réservées au writer dédié (si le domaine
     devient tokenisé : un SEUL writer fait les écritures structurées).
  3. WRITE TOKEN EXCEPTIONNEL — writers exceptionnels (ex. un autre domaine
     qui purge des tables par invitation — comme le writer de local qui peut
     supprimer des tables dans le buffer).
  4. WRAPPER SANS TOKEN VERS WRITER DÉDIÉ — fonctions qui ENVOIENT au writer
     dédié ce qu'il faut écrire dans le domaine (l'écriture passe par lui).

dialogue_agent : domaine ouvert → tout est en section 1 ; les sections 2-4
sont prévues (vide) au cas où le domaine doit se protéger du spam/attaque —
on ajoutera alors un writer qui « prend les signaux » (filtrage volume/rate).
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.dialogue_agent import db

Rows = List[Dict[str, Any]]


def _r(row) -> Optional[Dict[str, Any]]:
    return dict(row) if row is not None else None


def _rs(rows) -> Rows:
    return [dict(r) for r in rows]


# ────────────────────────────────────────────────────────────────────────────
# 1. WRITE NORMAL SANS TOKEN
# ────────────────────────────────────────────────────────────────────────────

def create_conversation(d: Db, project_id: str, team_id: int = -1,
                        title: str = "", conv_type: str = "chat",
                        parent_conv_id: Optional[int] = None,
                        created_by: int = 0) -> int:
    """Crée une conversation (racine) ou un fork de fil (parent_conv_id)."""
    cur = d._conn.execute(
        "INSERT INTO conversation (project_id, team_id, conv_type, title, "
        "parent_conv_id, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, team_id, conv_type, title, parent_conv_id, created_by))
    d._conn.commit()
    return cur.lastrowid


def post_message(d: Db, conv_id: int, agent_id: int, content: str,
                 msg_type: str = "text", team_id: int = -1,
                 project_id: str = "",
                 attachments: Optional[List[str]] = None) -> int:
    """Poste un message dans une conversation.

    agent_id NÉGATIF = point de terminaison humain (humain_as_agent) ;
    attachments = liste de paths génériques de référence."""
    att = json.dumps(attachments or [])
    cur = d._conn.execute(
        "INSERT INTO message (conv_id, agent_id, team_id, project_id, "
        "msg_type, content, attachments) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (conv_id, agent_id, team_id, project_id, msg_type, content, att))
    d._conn.commit()
    return cur.lastrowid


def send_direct(d: Db, agent_from: int, agent_to: int, content: str,
                kind: str = "request", attachment: str = "",
                interruption: str = "none", project_id: str = "") -> int:
    """Message direct 1:1 (inter-agent ou humain négatif → agent)."""
    cur = d._conn.execute(
        "INSERT INTO direct_message (agent_from, agent_to, project_id, kind, "
        "content, attachment, interruption) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (agent_from, agent_to, project_id, kind, content, attachment,
         interruption))
    d._conn.commit()
    return cur.lastrowid


def send_broadcast(d: Db, agents: List[int], content: str,
                   agent_from: int = 0, kind: str = "broadcast",
                   interruption: str = "none", project_id: str = "",
                   entrypoint_type: str = "") -> Dict[str, Any]:
    """Broadcast : UNE ligne direct_message par membre concerné.

    Le TYPE d'entrypoint du broadcast est défini comme une fonction côté
    FSM (entrypoint_type, ex. 'wake_for_tasks') : si fourni, le flag
    interruption='entrypoint' est posé — le FSM gérera l'entrypoint (plus
    tard) ; sinon interruption reste telle quelle."""
    if not agents:
        return {"ok": False, "error": "aucun destinataire"}
    inter = "entrypoint" if entrypoint_type else interruption
    ids = []
    for to in agents:
        ids.append(send_direct(d, agent_from, to, content, kind=kind,
                               interruption=inter, project_id=project_id))
    return {"ok": True, "sent": len(ids), "dm_ids": ids}


def mark_received(d: Db, dm_id: int) -> None:
    """Marque un direct_message comme reçu (flag du flusher + protocole)."""
    d._conn.execute("UPDATE direct_message SET received = 1 WHERE dm_id = ?",
                    (dm_id,))
    d._conn.commit()


def mark_received_all(d: Db, agent_to: int) -> int:
    """Marque reçus tous les messages d'un destinataire (retourne le compte)."""
    cur = d._conn.execute(
        "UPDATE direct_message SET received = 1 "
        "WHERE agent_to = ? AND received = 0", (agent_to,))
    d._conn.commit()
    return cur.rowcount


def ask_human(d: Db, question: str, agent_id: int, project_id: str = "",
              team_id: int = -1, task_id: Optional[int] = None,
              sub_task_id: Optional[int] = None,
              options_json: Optional[str] = None,
              choice_id: str = "") -> str:
    """Escalade humain : pose une question asynchrone (status pending).

    L'humain répond via read/write (answer_human) ; le réveil de la
    task/sub_task bloquée est fait par le supervisor (thaw)."""
    cid = choice_id or f"hc_{abs(agent_id)}"
    d._conn.execute(
        "INSERT OR IGNORE INTO human_choice (choice_id, project_id, team_id, "
        "agent_id, task_id, sub_task_id, question, options_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (cid, project_id, team_id, agent_id, task_id, sub_task_id, question,
         options_json))
    d._conn.commit()
    return cid


def answer_human(d: Db, choice_id: str, response: str) -> bool:
    """Réponse de l'humain (status answered) — le supervisor réveille la
    sub_task en attente (P10 watcher / task_supervisor)."""
    cur = d._conn.execute(
        "UPDATE human_choice SET status = 'answered', response = ?, "
        "answered_at = strftime('%s','now') WHERE choice_id = ? "
        "AND status = 'pending'", (response, choice_id))
    d._conn.commit()
    return cur.rowcount > 0


def consensus_ask(d: Db, question: str, id_creator: int,
                  project_id: str = "", team_id: int = 0,
                  options_json: Optional[str] = None,
                  max_tours: int = 5) -> int:
    """Ouvre une question de consensus (agents votent via consensus_vote)."""
    cur = d._conn.execute(
        "INSERT INTO question (project_id, team_id, question, id_creator, "
        "options_json, max_tours) VALUES (?, ?, ?, ?, ?, ?)",
        (project_id, team_id, question, id_creator, options_json, max_tours))
    d._conn.commit()
    return cur.lastrowid


def consensus_vote(d: Db, id_question: int, id_agent: int, contenu: str,
                   jugement: str = "", model_ref: str = "") -> int:
    """Vote d'un agent sur une question de consensus."""
    cur = d._conn.execute(
        "INSERT INTO reponse (id_question, id_agent, model_ref, contenu, "
        "jugement) VALUES (?, ?, ?, ?, ?)",
        (id_question, id_agent, model_ref, contenu, jugement))
    d._conn.commit()
    return cur.lastrowid


def consensus_status(d: Db, id_question: int, status: str) -> None:
    """awaiting / answered / cancelled (arbitrage fait par l'appelant)."""
    d._conn.execute(
        "UPDATE question SET status_answering = ?, "
        "answered_at = datetime('now') WHERE id_question = ?",
        (status, id_question))
    d._conn.commit()


def close_conversation(d: Db, conv_id: int) -> None:
    """Ferme une conversation (open → closed)."""
    d._conn.execute("UPDATE conversation SET status = 'closed' "
                    "WHERE conv_id = ?", (conv_id,))
    d._conn.commit()


def get_or_create_root_conversation(d: Db, project_id: str,
                                    title: str = "") -> int:
    """Conversation racine 'chat' du projet (crée si absente) — le chatroom
    par défaut des agents de ce projet."""
    r = d._conn.execute(
        "SELECT conv_id FROM conversation WHERE project_id = ? "
        "AND parent_conv_id IS NULL AND conv_type = 'chat' "
        "ORDER BY conv_id LIMIT 1", (project_id,)).fetchone()
    if r:
        return r["conv_id"]
    return create_conversation(d, project_id, title=title or project_id)


# ────────────────────────────────────────────────────────────────────────────
# 2. WRITE TOKEN WRITER DÉDIÉ — (VIDE pour dialogue_agent : domaine ouvert)
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# 3. WRITE TOKEN EXCEPTIONNEL — (VIDE pour dialogue_agent)
# ────────────────────────────────────────────────────────────────────────────

# ────────────────────────────────────────────────────────────────────────────
# 4. WRAPPER SANS TOKEN VERS WRITER DÉDIÉ — (VIDE pour dialogue_agent)
# ────────────────────────────────────────────────────────────────────────────