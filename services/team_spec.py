"""TeamSpec — Modèle déclaratif d'équipe (.team.yaml).

Une équipe regroupe des agents autour d'un objectif commun, avec une
topologie d'orchestration (hiérarchique, pair, DAG) et un director
optionnel pour la délégation automatique des tâches.

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


@dataclass
class TeamDirectorSpec:
    agent_name: str = ""
    role: str = "director"
    occupation: str = "continue"
    resources: Optional[Dict[str, Any]] = None
    config: Dict[str, Any] = field(default_factory=dict)
    provider_ref: str = ""
    model_ref: str = ""
    workflow: str = "orchestrate"  # orchestrate | delegate-only | manual


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
    director: Optional[TeamDirectorSpec] = None
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

        director_raw = raw.get("director")
        director = None
        if director_raw:
            director = TeamDirectorSpec(
                agent_name=director_raw.get("agent_name", ""),
                role=director_raw.get("role", "director"),
                occupation=director_raw.get("occupation", "continue"),
                resources=director_raw.get("resources"),
                config=director_raw.get("config", {}),
                provider_ref=director_raw.get("provider_ref", ""),
                model_ref=director_raw.get("model_ref", ""),
                workflow=director_raw.get("workflow", "orchestrate"),
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
            director=director,
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
            "director": {
                "agent_name": self.director.agent_name,
                "role": self.director.role,
                "workflow": self.director.workflow,
            } if self.director else None,
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
