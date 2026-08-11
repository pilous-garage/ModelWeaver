#!/usr/bin/env python3
"""catalogue_privileges_defaults — Autorisations par défaut.

À la création d'un agent/team, et à la mise à jour du team_leader, on injecte
les privilèges de base dans la table `privileges` du catalogue local :

  - member      → level 1000 : accès à son home + workspace (read/write),
                  aucun privilège root.
  - team_leader → level 2000 : accès home/workspace + droits d'exécution
                  et privileged (root ask) sur les paths de la team.

Chaque règle est ciblée par (agent_id, team). À la révocation (démission /
changement de leader), on RETIRE les règles correspondantes.

Niveaux (mode 4-groupes) : humain_with_root, humain, agent_with_root, agent.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# Home d'un agent : agent_home/{id} → read/write pour l'agent (position 2)
# et son leader (agent_with_root). mode 4-groupes: [h_wr, h, a_wr, a]
DEFAULT_AGENT_HOME = {
    "chemin_ref": "/agent_home/$1",
    "kind": "path",
    "level": 1000,
    "read": "--rr",       # agent_with_root=r, agent=r (l'agent et son leader lisent)
    "write": "--ww",
    "exec_": "--x-",
    "privileged": "----",
}

DEFAULT_WORKSPACE = {
    "chemin_ref": "/workspace/$1",
    "kind": "path",
    "level": 1000,
    "read": "--rr",
    "write": "--ww",
    "exec_": "--x-",
    "privileged": "----",
}

# team_leader (level 2000) : read/write/exec sur le home des membres de SA
# team + privilège root (ask) sur les paths d'installation système.
DEFAULT_TEAM_LEADER = {
    "chemin_ref": "/team/$1/*",
    "kind": "path",
    "level": 2000,
    "read": "--rr",
    "write": "--w-",
    "exec_": "--xx",
    "privileged": "--p-",
}


def _apply(chemin_ref: str, kind: str, level: int, read: str, write: str,
           exec_: str, privileged: str, agent_id: int = -1,
           team: int = -1) -> Dict[str, Any]:
    """Écrit une règle via le handler catalogue_local (writer)."""
    try:
        from services.api.handlers.catalogue_local import op_priv_create
        return op_priv_create({
            "chemin_ref": chemin_ref, "kind": kind, "level": level,
            "read": read, "write": write, "exec": exec_,
            "privileged": privileged, "agent_id": agent_id, "team": team,
            "token": "write_catalogue",
        })
    except Exception:
        return {"status": "error"}


def grant_agent(agent_id: int, team_id: Optional[int] = None) -> None:
    """Injection des privilèges member (level 1000) à la création d'un agent."""
    _apply(**DEFAULT_AGENT_HOME, agent_id=agent_id, team=team_id or -1)
    _apply(**DEFAULT_WORKSPACE, agent_id=agent_id, team=team_id or -1)


def grant_team_leader(team_id: int, leader_agent_id: Optional[int] = None) -> None:
    """Injection des privilèges team_leader (level 2000) à la promotion."""
    _apply(**DEFAULT_TEAM_LEADER, agent_id=leader_agent_id or -1, team=team_id)


def revoke_agent(agent_id: int) -> None:
    """Retrait des privilèges member d'un agent (déshydratation/démission)."""
    try:
        from services.api.handlers.catalogue_local import _get
        db = _get("w")
        db.conn.execute(
            "DELETE FROM privileges WHERE agent_id = ?", (int(agent_id),))
        db.conn.commit()
    except Exception:
        pass


def revoke_team_leader(team_id: int, leader_agent_id: Optional[int] = None) -> None:
    """Retrait des privilèges team_leader (changement de leader)."""
    try:
        from services.api.handlers.catalogue_local import _get
        db = _get("w")
        if leader_agent_id:
            db.conn.execute(
                "DELETE FROM privileges WHERE team = ? AND level = 2000 "
                "AND agent_id = ?", (int(team_id), int(leader_agent_id)))
        else:
            db.conn.execute(
                "DELETE FROM privileges WHERE team = ? AND level = 2000",
                (int(team_id),))
        db.conn.commit()
    except Exception:
        pass
