"""adapters — ponts vers le domaine dialogue_agent pour les libs agents.

Consensus : mêmes signatures que l'ancien ConsensusRepository (workspace.db)
pour que taskflow.py bascule sans toucher la logique d'arbitrage.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from modules.sqlite.base import Db
from modules.sqlite.dialogue_agent import db
from modules.sqlite.dialogue_agent import read as R
from modules.sqlite.dialogue_agent import write as W


class Consensus:
    """Question + réponses des answering_machine (table question/reponse)."""

    def __init__(self, d: Optional[Db] = None):
        self._d = d or db()

    # -- signatures identiques à ConsensusRepository (workspace legacy) ------

    def create_question(self, question: str, id_creator: int = 0,
                        options: Optional[list] = None,
                        max_tours: int = 5) -> Dict[str, Any]:
        qid = W.consensus_ask(self._d, question, id_creator=id_creator,
                              options_json=json.dumps(options or []) or None,
                              max_tours=max_tours)
        return {"id_question": qid, "question": question,
                "status_answering": "awaiting", "tour_courant": 1,
                "options": options or []}

    def get_question(self, id_question: int) -> Optional[Dict[str, Any]]:
        return R.question_get(self._d, id_question)

    def list_questions(self, status: str = "") -> List[Dict[str, Any]]:
        if status:
            return [q for q in R.questions_open(self._d)
                    if q["status_answering"] == status]
        return self._all_questions()

    def _all_questions(self) -> List[Dict[str, Any]]:
        rows = self._d._conn.execute(
            "SELECT * FROM question ORDER BY created_at").fetchall()
        return [dict(r) for r in rows]

    def set_status(self, id_question: int, status: str) -> None:
        W.consensus_status(self._d, id_question, status)

    def add_reponse(self, id_question: int, id_agent: int,
                    contenu: str, model_ref: str = "") -> Dict[str, Any]:
        rid = W.consensus_vote(self._d, id_question, id_agent, contenu,
                               model_ref=model_ref)
        return {"id_reponse": rid, "id_question": id_question,
                "id_agent": id_agent, "contenu": contenu}

    def get_reponses(self, id_question: int) -> List[Dict[str, Any]]:
        return R.reponses_of(self._d, id_question)

    def set_jugement(self, id_reponse: int, jugement: str) -> None:
        self._d._conn.execute(
            "UPDATE reponse SET jugement = ? WHERE id_reponse = ?",
            (jugement, id_reponse))
        self._d._conn.commit()


def chatroom_send_message(team_id=None, agent_id="", msg="",
                          workspace_id="", conv_id=None) -> Dict[str, Any]:
    """(pont résolution runtime) Poste un message dans la conversation racine
    'chat' du projet. workspace_id reçu pour compat (les agentes portent
    workspace_id dans variables_json) — utilisé comme project_id ; conv_id
    explicite si fourni (sinon conv racine du projet)."""
    try:
        from modules.sqlite.dialogue_agent import db as _dial
        from modules.sqlite.dialogue_agent import write as W
        pid = workspace_id or "mw-llm-code"
        d = _dial()
        if not conv_id:
            conv_id = W.get_or_create_root_conversation(d, pid)
        mid = W.post_message(d, conv_id, int(agent_id) if agent_id else 0,
                             str(msg), project_id=pid)
        d.close()
        return {"ok": True, "msg_id": mid, "conv_id": conv_id,
                "project_id": pid}
    except Exception as e:  # noqa: BLE001
        return {"status": "error", "error": str(e)}