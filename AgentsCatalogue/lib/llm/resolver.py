"""Resolver — Fusionne outils programmatiques + skills YAML en format OpenAI.

Prend en entrée :
  - Un Tool.Registry (outils Python)
  - Une liste de noms de bundles (skills YAML)
  - Des permissions (exclude list)

Produit en sortie :
  - Liste `[{type: "function", function: {name, description, parameters}}]`
  - Fonction d'exécution `execute(name, inputs, ws) → dict`

Inspiré de : opencode packages/opencode/src/tool/registry.ts
            et du bundle resolver existant (workflow/bundles.py)
"""

import copy
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Set, Tuple

from .tool import Def, Info, Registry
from .permission import Action, Rule, Ruleset

_SKILLS_DIR = Path(__file__).resolve().parent.parent.parent / "skills"
_BUNDLES_DIR = Path(__file__).resolve().parent.parent.parent / "bundles"


def resolve_bundle_permissions(
    bundle_names: List[str],
) -> Ruleset:
    """Charge et fusionne les permissions des bundles.

    Les bundles sont résolus dans l'ordre de la liste : un bundle
    ultérieur écrase les règles du précédent à spécificité égale.
    Retourne un Ruleset combiné.
    """
    import yaml as _yaml

    rules = []
    for bname in bundle_names:
        candidates = list(_BUNDLES_DIR.rglob(f"{bname}.yaml")) + list(_BUNDLES_DIR.rglob(f"{bname}.yml"))
        if not candidates:
            continue
        try:
            with open(candidates[0], encoding="utf-8") as f:
                bundle = _yaml.safe_load(f)
        except Exception:
            continue
        for perm in bundle.get("permissions", []):
            rules.append(Rule(
                tool=perm.get("tool", "*"),
                action=Action(perm.get("action", "ask")),
            ))
    return Ruleset(rules=rules)


def _resolve_from_bundles(
    bundle_names: List[str],
    seen: Optional[Set[str]] = None,
) -> List[Dict]:
    """Charge les outils depuis les bundles YAML + skills individuels.

    Chaque entrée est d'abord cherchée comme bundle (``bundles/*.yaml``).
    Si aucun bundle n'est trouvé, elle est traitée comme une référence
    directe de skill (``skills/*.skill.yaml``).
    """
    import yaml as _yaml

    bundles_dir = Path(__file__).resolve().parent.parent.parent / "bundles"
    result = []
    seen = seen or set()

    for bname in bundle_names:
        candidates = list(bundles_dir.rglob(f"{bname}.yaml")) + list(bundles_dir.rglob(f"{bname}.yml"))

        if candidates:
            # C'est un bundle → charger ses skills
            try:
                with open(candidates[0], encoding="utf-8") as f:
                    bundle = _yaml.safe_load(f)
            except Exception:
                continue
            for sref in bundle.get("skills", []):
                if sref in seen:
                    continue
                seen.add(sref)
                skill = _load_skill_yaml(sref)
                if skill is None:
                    continue
                tool = _skill_to_openai_tool(skill)
                if tool:
                    result.append(tool)
        else:
            # Pas un bundle → essayer comme skill direct
            if bname in seen:
                continue
            seen.add(bname)
            skill = _load_skill_yaml(bname)
            if skill is None:
                continue
            tool = _skill_to_openai_tool(skill)
            if tool:
                result.append(tool)

    return result


def _load_skill_yaml(sref: str) -> Optional[Dict]:
    """Charge un fichier .skill.yaml par son ref.

    Le fichier attendu suit la convention ::
        skills/{ref}.skill.yaml
    où ``ref`` = ``categorie/nom@v1``.
    """
    # Le nom du fichier est le ref complet + ".skill.yaml"
    # Ex: "file/read_file@v1" → "file/read_file@v1.skill.yaml"
    fname = f"{sref}.skill.yaml"
    candidates = list(_SKILLS_DIR.rglob(fname))
    if not candidates:
        # Fallback : recherche par nom court (compat ancien format)
        for p in _SKILLS_DIR.rglob("*.skill.yaml"):
            if p.stem == sref or p.stem.startswith(f"{sref}@"):
                candidates.append(p)
        if not candidates:
            return None
    try:
        import yaml as _yaml
        with open(candidates[0], encoding="utf-8") as f:
            return _yaml.safe_load(f)
    except Exception:
        return None


def _skill_to_openai_tool(skill: Dict) -> Optional[Dict]:
    """Convertit un skill YAML en format OpenAI tool.

    Reprend la logique de ``workflow/bundles._skill_to_tool()``.
    """
    name = skill.get("name", "").replace("/", "_").replace("@", "_").replace(".", "_")
    if not name:
        return None
    desc = skill.get("description", "")
    inputs = skill.get("inputs") or {}
    props = {}
    required = []
    for k, v in inputs.items():
        if v.get("injected"):
            continue
        prop = {"type": v.get("type", "string")}
        if v.get("description"):
            prop["description"] = v["description"][:100]
        if v.get("required"):
            required.append(k)
        if prop["type"] == "array":
            if "items" in v:
                prop["items"] = v["items"]
            else:
                prop["items"] = {"type": "string"}
        props[k] = prop
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def resolve_tools(
    registry: Optional[Registry] = None,
    bundle_names: Optional[List[str]] = None,
    exclude: Optional[Set[str]] = None,
) -> List[Dict]:
    """Résout tous les outils disponibles : registry + bundles.

    Returns:
        Liste au format OpenAI::
            [{"type": "function", "function": {"name": ..., ...}}]
    """
    excl = exclude or set()
    result = []

    if registry:
        result.extend(registry.to_openai_tools(exclude=excl))

    if bundle_names:
        seen = set()
        result.extend(_resolve_from_bundles(bundle_names, seen))

    return result


def make_dispatcher(
    registry: Optional[Registry] = None,
    ws: str = "",
) -> Callable[[str, dict], dict]:
    """Fabrique une fonction ``execute(name, inputs) → dict``.

    Essaie d'abord le registry, puis le système de skills YAML
    via ``call_skill()``.

    Returns:
        ``execute(name, inputs) -> dict``
    """
    from services.skill_manager import call_skill

    def execute(name: str, inputs: dict) -> dict:
        # Registry d'abord
        if registry:
            result = registry.execute(name, inputs, ws)
            if not result.get("ok") or "error" not in result:
                return result

        # Fallback skills YAML
        try:
            conv_name = name.replace("_v1", "@v1").replace("_", "/")
            result = call_skill(conv_name, inputs, home=ws)
            if isinstance(result, dict):
                return result
            return {"ok": True, "result": result}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    return execute
