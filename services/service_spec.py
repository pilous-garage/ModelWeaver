"""ServiceSpec — Modèle déclaratif de service (.service.yaml).

Un service est défini par un fichier YAML qui décrit :
  - Meta : name, version, description
  - Lancement : commande, args, mode (process/agent/once/loop)
  - Supervision : restart, max_restarts, healthcheck
  - Entrypoints : routes HTTP automatiquement enregistrées
  - Agent : config agent (optionnel, si mode=agent)
"""

from __future__ import annotations
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional


@dataclass
class HealthCheckSpec:
    type: str = "pid"  # pid, http, script
    port: Optional[int] = None
    endpoint: str = "/health"
    interval: int = 30
    timeout: int = 5
    command: Optional[str] = None  # for type=script


@dataclass
class EntrypointSpec:
    type: str  # agent-chat, agent-execute, inline, http-proxy, command
    description: str = ""
    method: str = "POST"
    path: Optional[str] = None  # for http-proxy
    handler: Optional[str] = None  # for inline (python code)
    entrypoint: Optional[str] = "main"  # for agent-execute


@dataclass
class LaunchSpec:
    command: str = ""
    args: List[str] = field(default_factory=list)
    workdir: Optional[str] = None
    env: Dict[str, str] = field(default_factory=dict)
    mode: str = "once"  # once, loop, on-demand


@dataclass
class AgentSpec:
    role: str = "chat"
    occupation: str = "noncontinue"
    config: Dict[str, Any] = field(default_factory=dict)
    # Comportement de consommation déclaré (Idée 18, O3) : burst | constant |
    # unknown (défaut). Le profileur MESURÉ surcharge cette déclaration (les
    # utilisateurs ne savent pas ce qu'ils font). Un agent burst libère vite
    # son allocation (réallocateur), un constant la conserve.
    llm_call_type: str = "unknown"

    @staticmethod
    def _normalize_call_type(v) -> str:
        v = (v or "unknown").strip().lower()
        return v if v in ("burst", "constant", "unknown") else "unknown"


@dataclass
class SupervisorSpec:
    restart: bool = False
    max_restarts: int = 10
    interval: float = 5.0


@dataclass
class ServiceSpec:
    name: str
    version: str = "0.1.0"
    description: str = ""
    parent: str = "agent-as-service"
    launch: LaunchSpec = field(default_factory=LaunchSpec)
    supervisor: SupervisorSpec = field(default_factory=SupervisorSpec)
    health: HealthCheckSpec = field(default_factory=HealthCheckSpec)
    entrypoints: Dict[str, EntrypointSpec] = field(default_factory=dict)
    agent: Optional[AgentSpec] = None

    @property
    def is_agent(self) -> bool:
        return self.agent is not None

    @property
    def svc_name(self) -> str:
        if self.is_agent:
            return f"chat:{self.name}"
        return self.name

    def entrypoint_routes(self) -> Dict[str, str]:
        return {f"service/{self.svc_name}/{ep}": ep
                for ep in self.entrypoints}

    @staticmethod
    def from_yaml(path: Path | str) -> "ServiceSpec":
        import yaml
        path = Path(path)
        raw = yaml.safe_load(path.read_text())
        if not raw:
            raise ValueError(f"Fichier vide: {path}")
        raw.setdefault("version", "0.1.0")
        raw.setdefault("description", "")
        raw.setdefault("parent", "agent-as-service")

        launch_raw = raw.get("launch", {}) or {}
        launch = LaunchSpec(
            command=launch_raw.get("command", ""),
            args=launch_raw.get("args", []),
            workdir=launch_raw.get("workdir"),
            env=launch_raw.get("env", {}),
            mode=launch_raw.get("mode", "once"),
        )

        sup_raw = raw.get("supervisor", {}) or {}
        supervisor = SupervisorSpec(
            restart=sup_raw.get("restart", False),
            max_restarts=sup_raw.get("max_restarts", 10),
            interval=sup_raw.get("interval", 5.0),
        )

        health_raw = raw.get("health", {}) or {}
        health = HealthCheckSpec(
            type=health_raw.get("type", "pid"),
            port=health_raw.get("port"),
            endpoint=health_raw.get("endpoint", "/health"),
            interval=health_raw.get("interval", 30),
            timeout=health_raw.get("timeout", 5),
            command=health_raw.get("command"),
        )

        entrypoints = {}
        for ep_name, ep_raw in (raw.get("entrypoints") or {}).items():
            entrypoints[ep_name] = EntrypointSpec(
                type=ep_raw.get("type", "agent-execute"),
                description=ep_raw.get("description", ""),
                method=ep_raw.get("method", "POST"),
                path=ep_raw.get("path"),
                handler=ep_raw.get("handler"),
                entrypoint=ep_raw.get("entrypoint", "main"),
            )

        agent_raw = raw.get("agent")
        agent = None
        if agent_raw:
            agent = AgentSpec(
                role=agent_raw.get("role", "chat"),
                occupation=agent_raw.get("occupation", "noncontinue"),
                config=agent_raw.get("config", {}),
                llm_call_type=AgentSpec._normalize_call_type(
                    agent_raw.get("llm_call_type")),
            )

        return ServiceSpec(
            name=raw["name"],
            version=raw.get("version", "0.1.0"),
            description=raw.get("description", ""),
            parent=raw.get("parent", "agent-as-service"),
            launch=launch,
            supervisor=supervisor,
            health=health,
            entrypoints=entrypoints,
            agent=agent,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "svc_name": self.svc_name,
            "version": self.version,
            "description": self.description,
            "parent": self.parent,
            "mode": self.launch.mode,
            "is_agent": self.is_agent,
            "entrypoints": list(self.entrypoints.keys()),
            "restart": self.supervisor.restart,
            "agent": {
                "role": self.agent.role,
                "occupation": self.agent.occupation,
                "llm_call_type": self.agent.llm_call_type,
            } if self.agent else None,
        }
