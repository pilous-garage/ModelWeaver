"""Bundle resolver — charge les bundles et résout les skills en outils OpenAI."""

from pathlib import Path
from typing import Any, Dict, List, Optional
import yaml as _yaml


_BUNDLES_DIR = Path(__file__).parent.parent.parent / "bundles"
_SKILLS_DIR = Path(__file__).parent.parent.parent / "skills"


def _load_bundle(name: str) -> Optional[Dict]:
    candidates = list(_BUNDLES_DIR.rglob(f"{name}.yaml")) + list(_BUNDLES_DIR.rglob(f"{name}.yml"))
    if not candidates:
        return None
    try:
        with open(candidates[0], encoding="utf-8") as f:
            return _yaml.safe_load(f)
    except Exception:
        return None


def _load_skill(ref: str) -> Optional[Dict]:
    parts = ref.replace("@v1", "").split("/")
    candidates = list(_SKILLS_DIR.rglob(f"{parts[-1]}*.skill.yaml"))
    if not candidates:
        return None
    try:
        with open(candidates[0], encoding="utf-8") as f:
            return _yaml.safe_load(f)
    except Exception:
        return None


def _skill_to_tool(skill: Dict) -> Optional[Dict]:
    name = skill.get("name", "").replace("/", "_").replace("@", "_").replace(".", "_")
    if not name:
        return None
    desc = skill.get("description", "")
    inputs = skill.get("inputs", {})
    props = {}
    required = []
    for k, v in inputs.items():
        if v.get("injected"):
            continue
        prop = {"type": v.get("type", "string")}
        if v.get("description"):
            prop["description"] = v["description"][:100]  # descriptions courtes
        if not v.get("required"):
            prop["nullable"] = True  # compatible Groq/Anthropic/OpenAI
        else:
            required.append(k)
        # Si le type est array, ajouter items
        if prop["type"] == "array" and "items" in v:
            prop["items"] = v["items"]
        props[k] = prop
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def resolve(bundle_names: List[str]) -> List[Dict]:
    """Résout une liste de noms de bundle en outils OpenAI.

    Retourne une liste de dicts au format OpenAI tool.
    """
    tools = []
    seen = set()
    for bname in bundle_names:
        bundle = _load_bundle(bname)
        if bundle is None:
            continue
        for sref in bundle.get("skills", []):
            if sref in seen:
                continue
            seen.add(sref)
            skill = _load_skill(sref)
            if skill is None:
                continue
            tool = _skill_to_tool(skill)
            if tool:
                tools.append(tool)
    return tools