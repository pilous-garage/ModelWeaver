"""TeamSpec — Modèle déclaratif d'équipe (.team.yaml).

Une équipe regroupe des agents autour d'un objectif commun, avec une
topologie d'orchestration (hiérarchique, pair, DAG) et un
team_leader (chef d'équipe) responsable de la coordination.

Chaque agent (et en particulier le team_leader) est unique à son
équipe : un même agent_name ne peut pas être échangé entre projets.

Usage:
    spec = TeamSpec.from_yaml("services/manifests/teams/bug-busters.team.yaml")
    print(spec.name, spec.members)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class TeamMemberSpec:
    agent_name: str
    role: str = "worker"
    occupation: str = "noncontinue"
    resources: Optional[Dict[str, Any]] = None
    config: Dict[str, Any] = field(default_factory=dict)
    provider_ref: str = ""
    model_ref: str = ""
    ref: str = ""  # Nom de l'agent catalogue (ex. "greedy-coder") — la team référence, ne définit pas.


@dataclass
class TeamLeaderSpec:
    """Chef d'équipe — agent responsable de l'orchestration du projet.

    Unique à une équipe : le team_leader d'un projet ne doit PAS être
    interchangeable avec le team_leader d'un autre projet.
    """
    agent_name: str = ""
    role: str = "team_leader"
    occupation: str = "continue"
    resources: Optional[Dict[str, Any]] = None
    config: Dict[str, Any] = field(default_factory=dict)
    provider_ref: str = ""
    model_ref: str = ""
    workflow: str = "orchestrate"  # orchestrate | delegate-only | manual
    ref: str = ""  # Nom de l'agent catalogue (ex. "chat-pilot") — la team référence, ne définit pas.


@dataclass
class TeamResourcesSpec:
    budget_per_hour: float = 0.0
    max_concurrent: int = 10
    llm_quota_per_day: int = 0


@dataclass
class TeamSpec:
    name: str
    version: str = "0.1.0"
    description: str = ""
    workspace_id: str = ""
    topology: str = "hierarchical"  # hierarchical | peer | dag
    team_leader: Optional[TeamLeaderSpec] = None
    members: List[TeamMemberSpec] = field(default_factory=list)
    resources: TeamResourcesSpec = field(default_factory=TeamResourcesSpec)

    @property
    def team_name(self) -> str:
        return f"team:{self.name}"

    @staticmethod
    def from_yaml(path: Path | str) -> "TeamSpec":
        import yaml
        path = Path(path)
        raw = yaml.safe_load(path.read_text())
        if not raw:
            raise ValueError(f"Fichier vide: {path}")

        leader_raw = raw.get("director") or raw.get("team_leader") or raw.get("leader")
        team_leader = None
        if leader_raw:
            team_leader = TeamLeaderSpec(
                agent_name=leader_raw.get("agent_name", ""),
                role=leader_raw.get("role", "team_leader"),
                occupation=leader_raw.get("occupation", "continue"),
                resources=leader_raw.get("resources"),
                config=leader_raw.get("config", {}),
                provider_ref=leader_raw.get("provider_ref", ""),
                model_ref=leader_raw.get("model_ref", ""),
                workflow=leader_raw.get("workflow", "orchestrate"),
                ref=leader_raw.get("ref", ""),
            )

        members = []
        for m_raw in raw.get("members", []):
            members.append(TeamMemberSpec(
                agent_name=m_raw.get("agent_name", ""),
                role=m_raw.get("role", "worker"),
                occupation=m_raw.get("occupation", "noncontinue"),
                resources=m_raw.get("resources"),
                config=m_raw.get("config", {}),
                provider_ref=m_raw.get("provider_ref", ""),
                model_ref=m_raw.get("model_ref", ""),
                ref=m_raw.get("ref", ""),
            ))

        res_raw = raw.get("resources", {}) or {}
        resources = TeamResourcesSpec(
            budget_per_hour=float(res_raw.get("budget_per_hour", 0)),
            max_concurrent=int(res_raw.get("max_concurrent", 10)),
            llm_quota_per_day=int(res_raw.get("llm_quota_per_day", 0)),
        )

        return TeamSpec(
            name=raw["name"],
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            workspace_id=raw.get("workspace_id", ""),
            topology=raw.get("topology", "hierarchical"),
            team_leader=team_leader,
            members=members,
            resources=resources,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "team_name": self.team_name,
            "version": self.version,
            "description": self.description,
            "workspace_id": self.workspace_id,
            "topology": self.topology,
            "team_leader": {
                "agent_name": self.team_leader.agent_name,
                "role": self.team_leader.role,
                "workflow": self.team_leader.workflow,
            } if self.team_leader else None,
            "members": [
                {"agent_name": m.agent_name, "role": m.role}
                for m in self.members
            ],
            "resources": {
                "budget_per_hour": self.resources.budget_per_hour,
                "max_concurrent": self.resources.max_concurrent,
                "llm_quota_per_day": self.resources.llm_quota_per_day,
            },
        }
