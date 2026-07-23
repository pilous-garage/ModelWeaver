"""Catalogue Agents (V0.7) — agents complets + génération inline.

Un agent complet = fichier YAML dans AgentsCatalogue/agents/{nom}.yaml.

Deux formes coexistent :
- FORME NORMALE (source, éditable) : référence les catalogues par nom
  (role, personality, behavior, skills). Ex:
      name: mon_agent
      role: codeur
      personality: curieux          # ref catalogue personnalités
      behavior: mon_comportement     # ref catalogue behaviors
      skills: [system/write_file@v1]
- FORME INLINE (compilée, lue par le FSM) : tout résolu en valeur,
  self-contained. Ecrit dans agents/{nom}.inline.yaml.
      personality: {tone, system_prompt}
      workflow: {steps: [...]}

Le FSM lit le .inline.yaml (efficacité : pas de résolution runtime).
L'IDE affiche le normal, peut basculer sur l'inline, et save les 2.
"""

import yaml
from pathlib import Path
from typing import Any, Dict

from services.api.catalogue_api import (
    _CATALOGUE, _read_yaml, _write_yaml, _slug,
    op_catalogue_personalities_get, op_catalogue_behaviors_get,
)

_AGENTS_DIR = _CATALOGUE / "agents"


# ── Résolution normal -> inline ──

def resolve_personality(ref_or_dict):
    if isinstance(ref_or_dict, dict):
        return {
            "tone": ref_or_dict.get("tone", ""),
            "system_prompt": ref_or_dict.get("system_prompt", ""),
        }
    if isinstance(ref_or_dict, str) and ref_or_dict:
        try:
            res = op_catalogue_personalities_get({"name": ref_or_dict})
            p = res.get("personality", {})
            return {"tone": p.get("tone", ""), "system_prompt": p.get("system_prompt", "")}
        except Exception:
            return {"tone": "", "system_prompt": ""}
    return {"tone": "", "system_prompt": ""}


def resolve_behavior_steps(ref_or_dict):
    if isinstance(ref_or_dict, dict):
        return ref_or_dict.get("steps", ref_or_dict.get("workflow", {}).get("steps", []))
    if isinstance(ref_or_dict, str) and ref_or_dict:
        try:
            res = op_catalogue_behaviors_get({"name": ref_or_dict})
            b = res.get("behavior", {})
            return b.get("steps", [])
        except Exception:
            return []
    return []


def _migrate_entrypoints(normal: Dict[str, Any]) -> Dict[str, Any]:
    """Si le YAML a encore l'ancien format `workflow.steps`, le migre vers
    `entrypoints.main.steps`. Renvoie le dict entrypoints."""
    entrypoints = normal.get("entrypoints")
    if isinstance(entrypoints, dict) and len(entrypoints) > 0:
        return entrypoints
    steps = resolve_behavior_steps(normal.get("behavior")) or normal.get("workflow", {}).get("steps", [])
    if steps:
        return {"main": {"steps": steps}}
    return {"main": {"steps": []}}


def inline_agent(normal: Dict[str, Any]) -> Dict[str, Any]:
    """Produit la forme inline (self-contained) à partir de la forme normale.

    Si le YAML source a `entrypoints`, on les préserve. Sinon on migre
    l'ancien `workflow.steps` vers `entrypoints.main.steps`.
    """
    personality = resolve_personality(normal.get("personality"))

    inline: Dict[str, Any] = {
        "name": normal.get("name", ""),
        "role": normal.get("role", ""),
        "personality": personality,
        "skills": normal.get("skills", []),
        "entrypoints": _migrate_entrypoints(normal),
    }
    # Champs optionnels préservés
    for k in ("description", "contexts", "default_config", "model_requirements", "hooks"):
        if k in normal:
            inline[k] = normal[k]
    return inline


# ── Routes daemon ──

def op_catalogue_agents_list(params: dict) -> Dict[str, Any]:
    agents: List[Dict[str, Any]] = []
    _AGENTS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(_AGENTS_DIR.glob("*.agent.yaml")):
        if p.name.startswith(".") or p.name.endswith(".inline.agent.yaml"):
            continue
        try:
            data = _read_yaml(p)
            agents.append({
                "name": data.get("name") or p.stem.removesuffix(".agent"),
                "role": data.get("role", ""),
                "description": data.get("description", ""),
                "has_inline": (p.with_suffix(".inline.agent.yaml")).exists(),
                "file": str(p.relative_to(_CATALOGUE)),
            })
        except Exception as e:
            agents.append({"name": p.stem.removesuffix(".agent"), "error": str(e)})
    return {"agents": agents}


def op_catalogue_agents_get(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _AGENTS_DIR / f"{_slug(name)}.agent.yaml"
    if not path.exists():
        raise FileNotFoundError(f"agent introuvable: {name}")
    inline_path = _AGENTS_DIR / f"{_slug(name)}.inline.agent.yaml"
    return {
        "agent": _read_yaml(path),
        "yaml": path.read_text(encoding="utf-8"),
        "inline_yaml": inline_path.read_text(encoding="utf-8") if inline_path.exists() else "",
        "has_inline": inline_path.exists(),
    }


def op_catalogue_agents_save(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    content = params.get("yaml") or params.get("content")
    if not name or not content:
        raise ValueError("name + yaml requis")
    data = yaml.safe_load(content)
    fname = data.get("name") or name
    path = _AGENTS_DIR / f"{_slug(fname)}.agent.yaml"
    _write_yaml(path, data)
    # Génère toujours l'inline à côté
    inline = inline_agent(data)
    inline_path = _AGENTS_DIR / f"{_slug(fname)}.inline.agent.yaml"
    _write_yaml(inline_path, inline)
    return {"status": "ok", "file": str(path.relative_to(_CATALOGUE)),
            "inline_file": str(inline_path.relative_to(_CATALOGUE))}


def op_catalogue_agents_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _AGENTS_DIR / f"{_slug(name)}.agent.yaml"
    if not path.exists():
        raise FileNotFoundError(f"agent introuvable: {name}")
    path.unlink()
    inline_path = _AGENTS_DIR / f"{_slug(name)}.inline.agent.yaml"
    if inline_path.exists():
        inline_path.unlink()
    return {"status": "ok", "deleted": str(path.relative_to(_CATALOGUE))}


def op_catalogue_agents_inline(params: dict) -> Dict[str, Any]:
    """Génère (ou régénère) le .inline.yaml à partir du normal sans le réécrire."""
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _AGENTS_DIR / f"{_slug(name)}.agent.yaml"
    if not path.exists():
        raise FileNotFoundError(f"agent introuvable: {name}")
    data = _read_yaml(path)
    inline = inline_agent(data)
    inline_path = _AGENTS_DIR / f"{_slug(name)}.inline.agent.yaml"
    _write_yaml(inline_path, inline)
    return {"status": "ok", "inline_yaml": inline_path.read_text(encoding="utf-8"),
            "inline_file": str(inline_path.relative_to(_CATALOGUE))}
