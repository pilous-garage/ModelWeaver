"""Outil delegate — délègue une requête à un sous-agent.

Deux modes de résolution :
1. agents.db (via AgentManager) — pour les agents déjà enregistrés
2. Catalogue YAML (fallback) — charge l'agent depuis son .agent.yaml
   et lance une boucle LLM simple avec son system_prompt + ses outils
"""

from __future__ import annotations
from typing import Any, Dict, List, Optional, Set


def delegate(inputs: Dict[str, Any], ws: str = "") -> Dict[str, Any]:
    agent_name = inputs.get("agent_name", "")
    request = inputs.get("request", "")
    entrypoint = inputs.get("entrypoint", "main")
    provider = inputs.get("provider_ref", "")
    model = inputs.get("model_ref", "")

    if not agent_name:
        return {"ok": False, "error": "agent_name requis"}
    if not request:
        return {"ok": False, "error": "request requis"}

    result = _try_agent_db(agent_name, request, entrypoint, provider, model)
    if result is not None:
        return result

    return _try_catalogue_yaml(agent_name, request, provider, model, ws)


def _try_agent_db(
    agent_name: str, request: str, entrypoint: str,
    provider: str, model: str,
) -> Optional[Dict[str, Any]]:
    try:
        from modules.sql.db import AgentsDB
        from services.agent_manager.service import Agent, AgentManager
        db = AgentsDB()
        mgr = AgentManager(db=db)
        row = mgr.get_by_name(agent_name)
        if not row:
            return None
        agent = Agent.hydrate(row["agent_id"], db)
        res = agent.execute(
            request,
            provider_ref=provider or None,
            model_ref=model or None,
            entrypoint=entrypoint,
        )
        agent.dehydrate()
        return {"ok": True, "result": res}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _build_filtered_registry_from_ruleset(ruleset: Any) -> Any:
    """Crée un Registry filtré depuis un Ruleset.

    Le Registry singleton est auto-alimenté par ``tools/*.tool.yaml``.
    On copie ses outils dans le sous-registry filtré par permissions.
    """
    from AgentsCatalogue.lib.llm.tool import Registry, get_registry, load_tool_registry
    from AgentsCatalogue.lib.llm.permission import PermissionChecker

    sub_registry = Registry(permission_checker=PermissionChecker(ruleset=ruleset))
    main_registry = get_registry()
    for info in main_registry.list():
        sub_registry.add(info)
    return sub_registry


def _build_filtered_registry(
    permissions: List[dict],
) -> Any:
    """Crée un Registry filtré depuis une liste de permissions YAML.

    Le PermissionChecker intégré au Registry filtre automatiquement
    les outils ``deny`` lors de l'appel à ``to_openai_tools()``.
    """
    from AgentsCatalogue.lib.llm.permission import Ruleset

    ruleset = Ruleset.from_dict(permissions) if permissions else Ruleset.all_allow()
    return _build_filtered_registry_from_ruleset(ruleset)


def _try_catalogue_yaml(
    agent_name: str, request: str,
    provider: str, model: str, ws: str,
) -> Dict[str, Any]:
    try:
        from pathlib import Path
        import yaml
        from services._common import mw_home
        from AgentsCatalogue.lib.llm.loop import run, LoopConfig
        from AgentsCatalogue.lib.llm.resolver import resolve_bundle_permissions
        from AgentsCatalogue.lib.llm.permission import Ruleset

        # Cherche l'agent YAML : d'abord sous mw_home(), puis sous le projet
        _lib_dir = Path(__file__).resolve().parent
        _project_agents = _lib_dir.parent.parent / "agents"
        _home_agents = mw_home() / "AgentsCatalogue" / "agents"

        yaml_path = _home_agents / f"{agent_name}.agent.yaml"
        if not yaml_path.exists():
            yaml_path = _project_agents / f"{agent_name}.agent.yaml"
        if not yaml_path.exists():
            return {
                "ok": False,
                "error": f"agent '{agent_name}' introuvable (ni dans agents.db "
                         f"ni dans le catalogue)",
            }

        data = yaml.safe_load(yaml_path.read_text())
        system_prompt = (data.get("personality", {}) or {}).get("system_prompt", "")
        bundles = data.get("bundles", [])
        skills = data.get("skills", [])
        all_sources = bundles + skills  # bundles + skills individuels
        agent_permissions = data.get("permissions", [])
        agent_cfg = data.get("default_config", {}) or {}

        # Fusion : permissions des bundles + permissions agent (agent écrase bundle)
        bundle_ruleset = resolve_bundle_permissions(bundles)
        agent_ruleset = Ruleset.from_dict(agent_permissions) if agent_permissions else Ruleset()
        merged = Ruleset(rules=bundle_ruleset.rules + agent_ruleset.rules)
        registry = _build_filtered_registry_from_ruleset(merged)

        result = run(
            request=request,
            system_prompt=system_prompt,
            tools=registry,
            bundle_names=all_sources,
            ws=ws,
            cfg=LoopConfig(
                provider_ref=provider or None,
                model_ref=model or None,
                max_steps=agent_cfg.get("max_tokens", 4096),
                temperature=agent_cfg.get("temperature", 0.7),
                auto_assign_llm=False,
                fallback_on_error=False,
            ),
        )
        return {"ok": True, "result": result.output}

    except Exception as e:
        return {"ok": False, "error": f"fallback catalogue: {str(e)}"}



