"""Catalogue API — exposition des catalogues de l'Agent Sandbox (V0.7).

Quatre catalogues, tous persistés en fichiers YAML sous AgentsCatalogue/ :

- skills        : AgentsCatalogue/skills/{category}/{name}@v1.yaml  (fonctions)
- behaviors     : AgentsCatalogue/comportement/{name}.yaml          (workflow steps)
- personalities : AgentsCatalogue/personnalité/{name}.yaml          (tone + system_prompt)
- roles         : AgentsCatalogue/rôles/{name}.yaml                 (via RoleManager)

Chaque handler suit la convention daemon : (params) -> payload(dict).
Le daemon encapsule le retour dans {"ok":..., "result": payload}.
En cas d'erreur, on lève une exception (le daemon renvoie 500 + error).
"""

import yaml
from pathlib import Path
from typing import Any, Dict, List

# Ancrage repo root : services/api/catalogue_api.py -> parents[2] = racine.
_REPO_ROOT = Path(__file__).resolve().parents[2]
_CATALOGUE = _REPO_ROOT / "AgentsCatalogue"
_SKILLS_DIR = _CATALOGUE / "skills"
_BEHAVIORS_DIR = _CATALOGUE / "comportement"
_PERSONALITIES_DIR = _CATALOGUE / "personnalité"
_ROLES_DIR = _CATALOGUE / "rôles"


# ── Bibliothèques de fonctions (AgentsCatalogue/lib) — pour le hover IDE ──

def op_lib_list(params: dict) -> Dict[str, Any]:
    from AgentsCatalogue.lib import scan, list_all
    scan()
    return {"libs": list_all()}


def op_lib_resolve(params: dict) -> Dict[str, Any]:
    from AgentsCatalogue.lib import resolve
    ref = params.get("ref") or params.get("name")
    if not ref:
        raise ValueError("ref requis")
    return resolve(ref)


def op_lib_scan(params: dict) -> Dict[str, Any]:
    from AgentsCatalogue.lib import scan, list_all
    # force re-scan
    import AgentsCatalogue.lib as lib_mod
    lib_mod._SCAN_DONE = False
    scan()
    return {"count": len(list_all())}


# ── Helpers génériques ──

def _read_yaml(path: Path) -> Dict[str, Any]:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _write_yaml(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)


def _slug(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_") else "_" for c in name.strip()).strip("_").lower()


# ── Skills ──

def _skill_meta(path: Path) -> Dict[str, Any]:
    data = _read_yaml(path)
    impl = data.get("implementation", {})
    uses_llm = data.get("uses_llm", impl.get("type") == "llm")
    return {
        "name": data.get("name") or path.stem,
        "category": data.get("category", path.parent.name),
        "description": data.get("description", ""),
        "inputs": data.get("inputs", {}),
        "outputs": data.get("outputs", {}),
        "implementation": impl,
        "uses_llm": uses_llm,
        "file": str(path.relative_to(_CATALOGUE)),
    }


def op_catalogue_skills_list(params: dict) -> Dict[str, Any]:
    skills: List[Dict[str, Any]] = []
    if _SKILLS_DIR.exists():
        for p in sorted(_SKILLS_DIR.rglob("*.yaml")):
            if p.name.startswith("."):
                continue
            try:
                skills.append(_skill_meta(p))
            except Exception as e:
                skills.append({"name": p.stem, "error": str(e)})
    return {"skills": skills}


def op_catalogue_skills_get(params: dict) -> Dict[str, Any]:
    name = params.get("name") or params.get("ref")
    if not name:
        raise ValueError("name requis")
    candidate = _SKILLS_DIR
    for part in name.split("/"):
        candidate = candidate / part
    if not str(candidate).endswith(".yaml"):
        candidate = candidate.with_suffix(".yaml")
    if not candidate.exists():
        found = None
        for p in _SKILLS_DIR.rglob("*.yaml"):
            if p.stem == name.split("/")[-1]:
                found = p
                break
        if not found:
            raise FileNotFoundError(f"skill introuvable: {name}")
        candidate = found
    return {"skill": _skill_meta(candidate), "yaml": candidate.read_text(encoding="utf-8")}


def op_catalogue_skills_save(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    content = params.get("yaml") or params.get("content")
    if not name or not content:
        raise ValueError("name + yaml requis")
    data = yaml.safe_load(content)
    category = (data.get("category") or "system").split("/")[0]
    fname = data.get("name") or name
    if "@" not in Path(fname).stem:
        fname = f"{Path(fname).stem}@v1"
    path = _SKILLS_DIR / category / f"{fname}.yaml"
    _write_yaml(path, data)
    return {"status": "ok", "file": str(path.relative_to(_CATALOGUE))}


def op_catalogue_skills_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name") or params.get("ref")
    if not name:
        raise ValueError("name requis")
    found = None
    for p in _SKILLS_DIR.rglob("*.yaml"):
        if p.stem == name.split("/")[-1] or str(p.relative_to(_SKILLS_DIR)) == name:
            found = p
            break
    if not found:
        raise FileNotFoundError(f"skill introuvable: {name}")
    found.unlink()
    return {"status": "ok", "deleted": str(found.relative_to(_CATALOGUE))}


# ── Behaviors ──

def _behavior_meta(path: Path) -> Dict[str, Any]:
    data = _read_yaml(path)
    return {
        "name": data.get("name") or path.stem,
        "description": data.get("description", ""),
        "steps": data.get("workflow", {}).get("steps", data.get("steps", [])),
        "file": str(path.relative_to(_CATALOGUE)),
    }


def op_catalogue_behaviors_list(params: dict) -> Dict[str, Any]:
    behaviors: List[Dict[str, Any]] = []
    _BEHAVIORS_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(_BEHAVIORS_DIR.glob("*.yaml")):
        if p.name.startswith("."):
            continue
        try:
            behaviors.append(_behavior_meta(p))
        except Exception as e:
            behaviors.append({"name": p.stem, "error": str(e)})
    return {"behaviors": behaviors}


def op_catalogue_behaviors_get(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _BEHAVIORS_DIR / f"{_slug(name)}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"behavior introuvable: {name}")
    return {"behavior": _behavior_meta(path), "yaml": path.read_text(encoding="utf-8")}


def op_catalogue_behaviors_save(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    content = params.get("yaml") or params.get("content")
    if not name or not content:
        raise ValueError("name + yaml requis")
    data = yaml.safe_load(content)
    fname = data.get("name") or name
    path = _BEHAVIORS_DIR / f"{_slug(fname)}.yaml"
    _write_yaml(path, data)
    return {"status": "ok", "file": str(path.relative_to(_CATALOGUE))}


def op_catalogue_behaviors_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _BEHAVIORS_DIR / f"{_slug(name)}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"behavior introuvable: {name}")
    path.unlink()
    return {"status": "ok", "deleted": str(path.relative_to(_CATALOGUE))}


# ── Personalities ──

def _personality_meta(path: Path) -> Dict[str, Any]:
    data = _read_yaml(path)
    return {
        "name": data.get("name") or path.stem,
        "description": data.get("description", ""),
        "tone": data.get("tone", data.get("personality", {}).get("tone", "")),
        "system_prompt": data.get("system_prompt", data.get("personality", {}).get("system_prompt", "")),
        "file": str(path.relative_to(_CATALOGUE)),
    }


def op_catalogue_personalities_list(params: dict) -> Dict[str, Any]:
    personalities: List[Dict[str, Any]] = []
    _PERSONALITIES_DIR.mkdir(parents=True, exist_ok=True)
    for p in sorted(_PERSONALITIES_DIR.glob("*.yaml")):
        if p.name.startswith("."):
            continue
        try:
            personalities.append(_personality_meta(p))
        except Exception as e:
            personalities.append({"name": p.stem, "error": str(e)})
    return {"personalities": personalities}


def op_catalogue_personalities_get(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _PERSONALITIES_DIR / f"{_slug(name)}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"personality introuvable: {name}")
    return {"personality": _personality_meta(path), "yaml": path.read_text(encoding="utf-8")}


def op_catalogue_personalities_save(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    content = params.get("yaml") or params.get("content")
    if not name or not content:
        raise ValueError("name + yaml requis")
    data = yaml.safe_load(content)
    fname = data.get("name") or name
    path = _PERSONALITIES_DIR / f"{_slug(fname)}.yaml"
    _write_yaml(path, data)
    return {"status": "ok", "file": str(path.relative_to(_CATALOGUE))}


def op_catalogue_personalities_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    path = _PERSONALITIES_DIR / f"{_slug(name)}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"personality introuvable: {name}")
    path.unlink()
    return {"status": "ok", "deleted": str(path.relative_to(_CATALOGUE))}


# ── Roles (via RoleManager) ──

def _get_role_manager():
    from AgentsCatalogue.role_manager import RoleManager
    return RoleManager(_ROLES_DIR)


def op_catalogue_roles_list(params: dict) -> Dict[str, Any]:
    rm = _get_role_manager()
    roles = []
    for name in rm.list_roles():
        r = rm.get_role(name)
        if not r:
            continue
        roles.append({
            "name": r.name,
            "description": r.description,
            "class": r.raw.get("class", ""),
            "sub_class": r.raw.get("sub_class", ""),
            "classification": r.raw.get("classification", {}),
            "skills": r.skills,
            "model_requirements": r.model_requirements,
        })
    return {"roles": roles}


def op_catalogue_roles_get(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    rm = _get_role_manager()
    r = rm.get_role(name)
    if not r:
        raise FileNotFoundError(f"rôle introuvable: {name}")
    path = _ROLES_DIR / f"{name}.yaml"
    return {"role": r.to_dict(), "yaml": path.read_text(encoding="utf-8")}


def op_catalogue_roles_save(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    content = params.get("yaml") or params.get("content")
    if not name or not content:
        raise ValueError("name + yaml requis")
    from AgentsCatalogue.role_manager import RoleDefinition, RoleValidationError
    data = yaml.safe_load(content)
    data["name"] = data.get("name") or name
    definition = RoleDefinition(data)
    rm = _get_role_manager()
    rm.save_role(definition)
    return {"status": "ok", "file": f"rôles/{definition.name}.yaml"}


def op_catalogue_roles_delete(params: dict) -> Dict[str, Any]:
    name = params.get("name")
    if not name:
        raise ValueError("name requis")
    rm = _get_role_manager()
    if rm.delete_role(name):
        return {"status": "ok", "deleted": f"rôles/{name}.yaml"}
    raise FileNotFoundError(f"rôle introuvable: {name}")


# ── Export agrégé (pour l'IDE : tout charger en 1 appel) ──

def op_catalogue_all(params: dict) -> Dict[str, Any]:
    return {
        "skills": op_catalogue_skills_list({})["skills"],
        "behaviors": op_catalogue_behaviors_list({})["behaviors"],
        "personalities": op_catalogue_personalities_list({})["personalities"],
        "roles": op_catalogue_roles_list({})["roles"],
    }
