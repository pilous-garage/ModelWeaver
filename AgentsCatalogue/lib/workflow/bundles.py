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
    # Nom d'outil = dernier segment du skill (git/git_clone@v1 -> git_clone_v1).
    # Nom naturel pour le LLM, résoluble par la conversion inverse dans
    # workflow/autonomous.py (git_clone_v1 -> git_clone@v1 -> match par préfixe).
    sname = skill.get("name", "")
    name = sname.replace("/", "_").replace("@", "_").replace(".", "_")
    if "/" in sname:
        name = sname.split("/")[-1].replace("@", "_").replace(".", "_")
    if not name:
        return None
    desc = skill.get("description", "")
    inputs = skill.get("inputs", {})
    props = {}
    required = []
    for k, v in inputs.items():
        if v.get("injected"):
            continue
        prop = _normalize_type(v.get("type", "string"), v)
        if v.get("description"):
            prop["description"] = v["description"][:100]  # descriptions courtes
        if not v.get("required"):
            prop["nullable"] = True  # compatible Groq/Anthropic/OpenAI
        else:
            required.append(k)
        props[k] = prop
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _normalize_type(raw: str, spec: dict) -> dict:
    """Normalise les types « maison » des skills en JSON Schema standard.

    Ex. list[message] -> {"type": "array", "items": {"type": "object"}}
        int | null    -> {"type": "integer", "nullable": True}
        bool          -> {"type": "boolean"}
    """
    t = (raw or "string").strip()

    # Union nullable (ex: "int | null") -> type principal + nullable
    if " | " in t:
        parts = [p.strip() for p in t.split("|")]
        nullable = "null" in parts
        parts = [p for p in parts if p != "null"]
        t = parts[0] if parts else "string"
        out = _normalize_type(t, spec)
        out["nullable"] = True
        return out

    # Liste générique (ex: list[message], list[str], array)
    if t.startswith("list[") or t == "array":
        if t.startswith("list["):
            inner = t[5:-1].strip()
        else:
            inner = ""
        out: dict = {"type": "array"}
        if inner:
            out["items"] = _normalize_type(inner, spec)
        elif "items" in spec:
            out["items"] = spec["items"]
        return out

    # Mappings de type maison -> JSON Schema
    mapping = {
        "string": "string",
        "str": "string",
        "integer": "integer",
        "int": "integer",
        "number": "number",
        "float": "number",
        "bool": "boolean",
        "boolean": "boolean",
        "object": "object",
        "dict": "object",
        "any": "string",
        "null": "null",
    }
    return {"type": mapping.get(t, "string")}


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
            skill = _load_skill(sref)
            if skill is None:
                continue
            sname = skill.get("name", "")
            if not sname or sname in seen:
                continue
            seen.add(sname)
            tool = _skill_to_tool(skill)
            if tool:
                tools.append(tool)
    return tools