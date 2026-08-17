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
    # Sous-agents déclarés par ce membre (références de définition) : instanciés
    # au setup avec le HOME du maître. ex. [{"agent_name":"explore","ref":"greedy-explore","role":"explorateur"}]
    sub_agents: List[Dict[str, Any]] = field(default_factory=list)


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
    project_id: str = ""   # repo git central cloné par les greedy (ex mw-swarm)
    topology: str = "hierarchical"  # hierarchical | peer | dag
    team_leader: Optional[TeamLeaderSpec] = None
    members: List[TeamMemberSpec] = field(default_factory=list)
    resources: TeamResourcesSpec = field(default_factory=TeamResourcesSpec)
    # Tableau de règles du task_supervisor : (in_type, in_tag) → (out_type,
    # out_tag). OBLIGATOIRE à la déclaration d'une team (V0.15 taskflow).
    supervisor_rules: List[Dict[str, Any]] = field(default_factory=list)
    # Stratégie d'allocation des ressources du PIPELINE (Idée 18, O4) :
    # répartition du budget global de la découpe par étape de sub_task.
    # Format : {sub_task_type: poids} — les poids se normalisent sur leur
    # somme. Défaut : 40% coding, 20% reviewing, 20% testing, 10% planning,
    # 10% merging (budgets de référence, surtout pour les gratuits).
    allocation_strategy: Dict[str, float] = field(default_factory=dict)
    # Fichier ouvert (Idée 18, O1) : le chemin du manifest + l'état dirty.
    _source_path: str = ""
    _dirty: bool = field(default=False, compare=False)

    @property
    def source_path(self) -> str:
        return self._source_path

    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_dirty(self):
        """Marque le spec comme modifié via ModelWeaver (réécriture attendue)."""
        self._dirty = True

    def save(self) -> dict:
        """Persiste le spec vers son fichier ouvert (manifest_store).

        Règles : on n'écrit QUE si le spec a été modifié via ModelWeaver
        (mark_dirty) ET que le fichier n'a pas été réécrit depuis le chargement
        (garde anti-écrasement). Best-effort."""
        if not self._source_path:
            return {"ok": False, "written": False,
                    "reason": "pas de fichier source (spec construit en code)"}
        if not self._dirty:
            return {"ok": True, "written": False,
                    "reason": "aucune modification via ModelWeaver"}
        from services.manifest_store import write_yaml, open_manifest
        om = open_manifest(self._source_path)
        om.mark_dirty()
        res = write_yaml(self._source_path, self.to_yaml_dict())
        if res.get("written"):
            self._dirty = False
        return res

    def to_yaml_dict(self) -> dict:
        """Reproduit le format YAML du manifest team (source de vérité disque).

        C'est ce dict qu'on réécrit (uniquement sur modif via ModelWeaver).
        On conserve les champs d'origine + les mutations (membres, leader,
        supervisor_rules, allocation_strategy)."""
        d = {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "workspace_id": self.workspace_id,
            "project_id": self.project_id,
            "topology": self.topology,
            "team_leader": {
                "agent_name": self.team_leader.agent_name,
                "role": self.team_leader.role,
                "occupation": self.team_leader.occupation,
                "resources": self.team_leader.resources,
                "config": self.team_leader.config,
                "provider_ref": self.team_leader.provider_ref,
                "model_ref": self.team_leader.model_ref,
                "workflow": self.team_leader.workflow,
                "ref": self.team_leader.ref,
            } if self.team_leader else None,
            "members": [
                {
                    "agent_name": m.agent_name,
                    "role": m.role,
                    "occupation": m.occupation,
                    "resources": m.resources,
                    "config": m.config,
                    "provider_ref": m.provider_ref,
                    "model_ref": m.model_ref,
                    "ref": m.ref,
                    "sub_agents": m.sub_agents,
                }
                for m in self.members
            ],
            "resources": {
                "budget_per_hour": self.resources.budget_per_hour,
                "max_concurrent": self.resources.max_concurrent,
                "llm_quota_per_day": self.resources.llm_quota_per_day,
            },
            "supervisor_rules": self.supervisor_rules,
            "allocation_strategy": self.allocation_strategy,
        }
        return d

    @property
    def team_name(self) -> str:
        return f"team:{self.name}"

    @staticmethod
    def from_yaml(path: Path | str) -> "TeamSpec":
        import yaml
        path = Path(path)
        # Fichier ouvert (Idée 18, O1) : on passe par manifest_store pour que le
        # spec porte l'état (content, hash, dirty) et que save() puisse vérifier
        # la garde anti-écrasement. Le contenu YAML vient du disque (source).
        from services.manifest_store import open_manifest
        om = open_manifest(path)
        raw = yaml.safe_load(om.content.decode() or path.read_text())
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
                sub_agents=m_raw.get("sub_agents", []) or [],
            ))

        res_raw = raw.get("resources", {}) or {}
        resources = TeamResourcesSpec(
            budget_per_hour=float(res_raw.get("budget_per_hour", 0)),
            max_concurrent=int(res_raw.get("max_concurrent", 10)),
            llm_quota_per_day=int(res_raw.get("llm_quota_per_day", 0)),
        )

        # Tableau de règles du task_supervisor — OBLIGATOIRE (V0.15).
        # Format : liste de {in_type, in_tag, out_type, out_tag}.
        supervisor_rules = raw.get("supervisor_rules")
        if supervisor_rules is None:
            raise ValueError(
                f"Team {raw.get('name', path)}: `supervisor_rules` manquant — "
                "le tableau de règles (in_type, in_tag) → (out_type, out_tag) "
                "est obligatoire à la déclaration d'une team (taskflow V0.15).")
        if not isinstance(supervisor_rules, list):
            raise ValueError(f"Team {raw.get('name', path)}: supervisor_rules doit être une liste")
        norm_rules = []
        for i, r in enumerate(supervisor_rules):
            if not isinstance(r, dict) or "in_type" not in r or "out_type" not in r:
                raise ValueError(
                    f"Team {raw.get('name', path)}: règle {i} invalide — "
                    "{in_type, in_tag, out_type, out_tag} requis")
            norm_rules.append({
                "in_type": r.get("in_type", ""),
                "in_tag": r.get("in_tag", ""),
                "out_type": r.get("out_type", ""),
                "out_tag": r.get("out_tag", ""),
            })

        return TeamSpec(
            name=raw["name"],
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            workspace_id=raw.get("workspace_id", ""),
            project_id=raw.get("project_id", raw.get("repo", "")),
            topology=raw.get("topology", "hierarchical"),
            team_leader=team_leader,
            members=members,
            resources=resources,
            supervisor_rules=norm_rules,
            allocation_strategy=raw.get("allocation_strategy") or {},
            _source_path=str(path),
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
            "supervisor_rules": self.supervisor_rules,
            "allocation_strategy": self.allocation_strategy,
        }
