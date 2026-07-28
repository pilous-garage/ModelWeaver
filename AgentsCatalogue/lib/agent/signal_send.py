"""agent/signal_send@v1 — Envoie un signal à un agent (wakeup, sleep, etc.)."""

from typing import Dict, Optional


def exec(inputs: dict, home: str) -> dict:
    """Envoie un signal à un agent via AgentManager.send_signal().

    inputs:
        agent_id: str — nom de l'agent destinataire
        signal_type: str — wakeup | sleep | kill | pause | resume | configure
        payload: dict (optionnel) — données additionnelles
    """
    agent_name = inputs.get("agent_id", "")
    if not agent_name:
        return {"ok": False, "error": "agent_id required"}

    signal_type = inputs.get("signal_type", "")
    if not signal_type:
        return {"ok": False, "error": "signal_type required"}

    payload = inputs.get("payload", {})

    try:
        from modules.sql.db import AgentsDB
        from services.agent_manager.service import AgentManager

        db = AgentsDB()
        mgr = AgentManager(db=db)

        # Résoudre le nom en agent_id
        row = db.conn.execute(
            "SELECT agent_id FROM agents WHERE name = ?", (agent_name,)
        ).fetchone()
        if not row:
            return {"ok": False, "error": f"agent '{agent_name}' introuvable"}

        agent_id = row["agent_id"]
        result = mgr.send_signal(agent_id, signal_type, payload)
        return {"ok": result.get("status") == "ok", "signal_id": result.get("signal_id"), "type": signal_type}
    except Exception as e:
        return {"ok": False, "error": str(e)}


__skills__ = ["exec"]