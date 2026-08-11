"""compile_yaml — Inline récursif skill/agent/team (yaml_simple → inline).

Le FSM ne reçoit que des agents INLINE : plus aucun appel catalogue à
l'exécution. Le passage yaml_simple → inline se fait ici, dans le service
utils (route ``utils/compile/yaml-inline``).

Principe (déclarations catalogue remontées au plus haut niveau) :
  - SKILL  : retourne la définition complète (contrat inputs/outputs + workflow
    le cas échéant). Déjà self-contained.
  - AGENT  : résout les méthodes (héritage ``extends`` + ``overrides``), attache
    la définition complète de chaque skill référencée (``step['skill']``) et
    inline récursivement les sous-agents (``sub_agents``) + les bodies
    (loops/switch condition, workflow de skills).
  - TEAM   : inline récursivement le leader et chaque membre (agents).

Format inline d'un agent :
  name, role, description, kind, bundles, model_requirements, contexts,
  default_config, methods (résolues héritage+overrides),
  sub_agents (inline récursifs), entrypoints (steps avec ``skill`` résolu).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from pathlib import Path

import yaml

# Racine du catalogue.
_CATALOGUE = Path(__file__).resolve().parent.parent.parent / "AgentsCatalogue"

# Champs "objet" inline. Attention à l'ordre des kwargs.
_METHOD_FIELDS = ("description", "inputs", "outputs", "implementation", "tags")


# --------------------------------------------------------------------------- #
# Chargement catalogue (léger, uniquement pour la résolution)
# --------------------------------------------------------------------------- #

def _load_yaml_file(path: Path) -> Optional[Dict[str, Any]]:
    try:
        data = yaml.safe_load(path.read_text())
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _load_kind(kind: str) -> Dict[str, Any]:
    """{ref → def} pour un type de catalogue (skill|agent). Team non utilisé."""
    result: Dict[str, Any] = {}
    if kind not in ("skills", "agents"):
        return result
    for p in sorted((_CATALOGUE / kind).rglob("*.yaml")):
        # skip .old, inline.*, etc.
        if ".old" in p.name or "inline." in p.name:
            continue
        data = _load_yaml_file(p)
        if data and data.get("name"):
            result[data["name"]] = data
    return result


def _skill_def(ref: str, cache: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Retourne la définition d'un skill référencé ``ref`` (ex
    "workspace/task_claim_next@v1") en normalisant la référence."""
    ref = ref.replace("/", "/", 1)  # no-op, garde pour clarté
    if ref in cache:
        return cache[ref]
    # recherche par suffixe (namespace/@version toléré)
    for key, val in cache.items():
        if key == ref or key.endswith("/" + ref) or key.startswith(ref + "/"):
            return val
    return None


def _default_skill_cache() -> Dict[str, Any]:
    return _load_kind("skills")


def _default_agent_cache() -> Dict[str, Any]:
    return _load_kind("agents")


# --------------------------------------------------------------------------- #
# Détection du kind
# --------------------------------------------------------------------------- #

def detect_kind(data: Dict[str, Any]) -> str:
    """skill | agent | team — par la présence de champs distinctifs."""
    if "implementation" in data or "workflow" in data or "inputs" in data:
        return "skill"
    if "entrypoints" in data or "methods" in data or "sub_agents" in data:
        return "agent"
    if "leader" in data or "director" in data or "members" in data:
        return "team"
    # fallback par nom de fichier / name
    return "agent"


# --------------------------------------------------------------------------- #
# Résolution méthodes (héritage simple/multiple + overrides)
# --------------------------------------------------------------------------- #

def _parent_defs(data: Dict[str, Any], agent_cache: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Retourne les définitions des parents directs (extends: A ou [B, C])."""
    ext = data.get("extends")
    if not ext:
        return []
    refs = ext if isinstance(ext, list) else [ext]
    out = []
    for ref in refs:
        if isinstance(ref, dict):   # parent défini inline (rare)
            out.append(ref)
            continue
        pdef = agent_cache.get(ref) or _skill_def(ref, {})
        if pdef:
            out.append(pdef)
    return out


def _collect_inherited(data: Dict[str, Any], agent_cache: Dict[str, Any],
                       field: str, own_keys: Optional[set] = None) -> Dict[str, Any]:
    """Fusionne un champ hérité (methods/entrypoints/…) des parents.

    Héritage multiple SANS recouvrement : si ≥2 parents définissent la MÊME
    clé ET que l'enfant ne la redéfinit pas → conflit (erreur). Un parent
    seul prime, l'enfant override toujours. ``own_keys`` = clés déjà définies
    par l'enfant (levées du contrôle de conflit)."""
    own_keys = own_keys or set()
    merged: Dict[str, Any] = {}
    for parent in _parent_defs(data, agent_cache):
        val = parent.get(field)
        if isinstance(val, list):
            # fusion de listes par `name` (ex. members)
            for item in val:
                nm = item.get("name") if isinstance(item, dict) else str(item)
                if nm in own_keys:
                    continue   # l'enfant la redéfinit → pas de conflit
                if nm in merged:
                    raise ValueError(
                        f"conflit d'héritage sur '{field}.{nm}' : défini par ≥2 "
                        f"parents — le redéfinir dans '{data.get('name')}' pour lever l'ambiguïté")
                merged[nm] = item
        elif isinstance(val, dict):
            for name, v in val.items():
                if name in own_keys:
                    continue   # l'enfant la redéfinit → pas de conflit
                if name in merged:
                    raise ValueError(
                        f"conflit d'héritage sur '{field}.{name}' : défini par ≥2 "
                        f"parents — le redéfinir dans '{data.get('name')}' pour lever l'ambiguïté")
                merged[name] = v
    return merged


def _resolve_methods(data: Dict[str, Any], agent_cache: Dict[str, Any]) -> Dict[str, Any]:
    """Méthodes d'un agent = héritées (extends simple/multiple, sans
    recouvrement) + siennes (les overrides écrasent l'héritage)."""
    methods = _collect_inherited(data, agent_cache, "methods",
                                 own_keys=set((data.get("methods") or {}).keys()))
    for name, m in (data.get("methods") or {}).items():
        methods[name] = dict(m)
    return methods


# --------------------------------------------------------------------------- #
# Inline d'un step / body / workflow
# --------------------------------------------------------------------------- #

def _inline_step(s: Dict[str, Any], skill_cache: Dict[str, Any],
                 agent_cache: Dict[str, Any],
                 local_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Step rendu self-contained : attach la skill def sur step['skill'],
    inline les bodies (loop/switch) et les steps de workflow de skills.

    ``local_cache`` : sub-skills définies AU PLUS HAUT NIVEAU de l'agent
    (prioritaires sur le catalogue)."""
    step = dict(s)
    t = step.get("type")
    local_cache = local_cache or {}

    # body structurels (loop / switch / if)
    for key in ("body", "else", "conditions", "then"):
        if key in step:
            if isinstance(step[key], dict) and "steps" in step[key]:
                step[key] = {"steps": [_inline_step(x, skill_cache, agent_cache,
                                                    local_cache)
                                       for x in step[key]["steps"]]}
            elif isinstance(step[key], list):
                step[key] = [_inline_step(x, skill_cache, agent_cache,
                                          local_cache)
                             for x in step[key]]

    # skill référencée (call) → définition attachée (sub-skill d'abord)
    if t == "call" and step.get("fn"):
        sk = local_cache.get(step["fn"]) or _skill_def(step["fn"], skill_cache)
        if sk:
            step["skill"] = inline_skill(sk, skill_cache, agent_cache, local_cache)

    # step structurel de type skill : workflow résolu inline
    if step.get("inner"):
        step["inner"] = _inline_steps(step["inner"], skill_cache, agent_cache,
                                      local_cache)
    return step


def _inline_steps(steps: List[Dict[str, Any]], skill_cache: Dict[str, Any],
                  agent_cache: Dict[str, Any],
                  local_cache: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    return [_inline_step(s, skill_cache, agent_cache, local_cache)
            for s in steps or []]


# --------------------------------------------------------------------------- #
# Inline par kind
# --------------------------------------------------------------------------- #

def inline_skill(data: Dict[str, Any], skill_cache: Dict[str, Any],
                 agent_cache: Dict[str, Any],
                 local_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Skill → définition complète, self-contained. Un workflow éventuel voit
    ses propres steps résolus inline (skills imbriquées)."""
    out = dict(data)
    wf = out.get("workflow")
    if wf and wf.get("steps"):
        out["workflow"] = {"steps": _inline_steps(wf["steps"], skill_cache,
                                                  agent_cache, local_cache)}
    out["kind"] = "skill"
    out["inline"] = True
    return out


def inline_agent(data: Dict[str, Any], skill_cache: Dict[str, Any],
                 agent_cache: Dict[str, Any]) -> Dict[str, Any]:
    """Agent → inline récursif : méthodes résolues (extends+overrides), skills
    attachées aux steps, sous-agents inline, bodies/workflows résolus.

    Les SUB-SKILLS (section `sub_skills:` définies au plus haut niveau de
    l'agent, sous forme de dicts {name: {impl…}}) sont disponibles pour tous
    les steps qui les référencent par leur name (prioritaires sur catalogue)."""
    out = dict(data)
    out["kind"] = "agent"
    out["inline"] = True
    out["methods"] = _resolve_methods(data, agent_cache)

    # sub-skills : {name → def} définies inline au plus haut niveau
    local_cache: Dict[str, Any] = {}
    for sk in (data.get("sub_skills") or []):
        if isinstance(sk, str):
            continue   # ref catalogue → résolue via skill_cache
        nm = sk.get("name") if isinstance(sk, dict) else None
        if nm:
            local_cache[nm] = sk
    if local_cache:
        out["sub_skills"] = list(local_cache.values())

    # entrypoints hérités (extends) fusionnés sans recouvrement + les siens
    entrypoints = _collect_inherited(data, agent_cache, "entrypoints",
                                     own_keys=set((data.get("entrypoints") or {}).keys()))
    for name, m in (data.get("entrypoints") or {}).items():
        entrypoints[name] = dict(m) if isinstance(m, dict) else m
    for ep_name, ep in entrypoints.items():
        if isinstance(ep, dict) and ep.get("steps"):
            ep["steps"] = _inline_steps(ep["steps"], skill_cache, agent_cache,
                                        local_cache)
    out["entrypoints"] = entrypoints

    # sous-agents hérités + les siens (inline récursif)
    subs = []
    sub_defs: Dict[str, Dict[str, Any]] = {}
    for parent in _parent_defs(data, agent_cache):
        for sub in (parent.get("sub_agents") or []):
            nm = sub.get("name") if isinstance(sub, dict) else sub
            if nm in sub_defs:
                raise ValueError(
                    f"conflit d'héritage sur 'sub_agents.{nm}' : défini par ≥2 "
                    f"parents — le redéfinir dans '{data.get('name')}' pour lever l'ambiguïté")
            sub_defs[nm] = sub
    for sub in (data.get("sub_agents") or []):
        nm = sub.get("name") if isinstance(sub, dict) else sub
        sub_defs[nm] = sub   # les siens écrasent (override)
    for sub in sub_defs.values():
        if isinstance(sub, str):
            sub = {"name": sub}
        sdef = sub.get("name") and (agent_cache.get(sub["name"]) or {})
        base = dict(sdef or {})
        # garde le `name` (définition inline ou référence catalogue)
        if sub.get("name"):
            base["name"] = sub["name"]
        base.update({k: v for k, v in sub.items() if k != "name" and v is not None})
        subs.append(inline_agent(base, skill_cache, agent_cache))
    if subs:
        out["sub_agents"] = subs
    return out


def _team_cache() -> Dict[str, Any]:
    """{name → def} pour les teams du catalogue."""
    result: Dict[str, Any] = {}
    root = _CATALOGUE / "teams"
    if not root.is_dir():
        return result
    for p in sorted(root.rglob("*.yaml")):
        if ".old" in p.name or "inline." in p.name:
            continue
        data = _load_yaml_file(p)
        if data and data.get("name"):
            result[data["name"]] = data
    return result


def inline_team(data: Dict[str, Any], skill_cache: Dict[str, Any],
                agent_cache: Dict[str, Any],
                team_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Team → director + members inline récursivement (format réel).

    Héritage : `extends` (simple/multiple, sans recouvrement) fusionne
    director/members/topology/workspace. Les siens écrasent."""
    team_cache = team_cache if team_cache is not None else _team_cache()
    out = dict(data)
    out["kind"] = "team"
    out["inline"] = True

    def _agent_or(data_: Dict[str, Any]) -> Dict[str, Any]:
        return inline_agent(data_, skill_cache, agent_cache)

    # héritage des champs structurants
    for field in ("members", "director", "leader", "topology", "workspace_id",
                  "resources"):
        if field in ("members", "director", "leader"):
            merged = _collect_inherited(
                data, team_cache, field,
                own_keys=set((data.get(field).get("name")
                              if isinstance(data.get(field), dict) else
                              (m.get("name") if isinstance(m, dict) else str(m)
                               for m in (data.get(field) or [])))))
        else:
            # scalaires/objets simples : premier parent qui le définit
            merged = {}
            for parent in _parent_defs(data, team_cache):
                if parent.get(field) is not None:
                    merged = parent[field]
                    break
        own = data.get(field)
        if own is not None:
            if field == "members" and isinstance(merged, dict):
                # fusion liste héritée ({name: member}) + liste propre :
                # les siens écrasent (même name) ou s'ajoutent.
                for m in (own if isinstance(own, list) else [own]):
                    nm = m.get("name") if isinstance(m, dict) else str(m)
                    merged[nm] = m
                merged = list(merged.values())
            elif isinstance(merged, dict) and isinstance(own, dict):
                merged.update(own)
            else:
                merged = own
        elif field == "members" and data.get("members") is None:
            merged = merged or []
        if field == "members" and isinstance(merged, dict):
            # _collect_inherited retourne {name: member} → liste
            merged = list(merged.values())
        if merged:
            out[field] = merged

    # director (format réel) / leader (alias) → agent inline
    director = out.get("director")
    if isinstance(director, dict):
        out["director"] = _agent_or(director)
    elif isinstance(director, str):
        base = dict(agent_cache.get(director) or {})
        base.setdefault("name", director)
        out["director"] = _agent_or(base)
    leader = out.get("leader")
    if isinstance(leader, dict):
        out["leader"] = _agent_or(leader)
    elif isinstance(leader, str):
        base = dict(agent_cache.get(leader) or {})
        base.setdefault("name", leader)
        out["leader"] = _agent_or(base)

    # members → agents inline (déjà fusionnés héritage+siens)
    members = []
    for m in (out.get("members") or []):
        if isinstance(m, str):
            m = {"name": m}
        sdef = m.get("name") and (agent_cache.get(m["name"]) or {})
        base = dict(sdef or {})
        if m.get("name"):
            base["name"] = m["name"]
        base.update({k: v for k, v in m.items() if k != "name" and v is not None})
        members.append(_agent_or(base))
    if members:
        out["members"] = members
    return out


# --------------------------------------------------------------------------- #
# Entrée principale
# --------------------------------------------------------------------------- #

def inline_document(data: Dict[str, Any], kind: Optional[str] = None,
                    skill_cache: Optional[Dict[str, Any]] = None,
                    agent_cache: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Inline un document yaml_simple (skill/agent/team) en document self-contained.

    ``data`` : dict yaml. ``kind`` : "skill" | "agent" | "team" (auto-détecté si
    None). Les caches de catalogue peuvent être injectés (sinon chargés ici).
    """
    kind = kind or detect_kind(data)
    skill_cache = skill_cache if skill_cache is not None else _default_skill_cache()
    agent_cache = agent_cache if agent_cache is not None else _default_agent_cache()

    if kind == "skill":
        return inline_skill(data, skill_cache, agent_cache)
    if kind == "team":
        return inline_team(data, skill_cache, agent_cache)
    return inline_agent(data, skill_cache, agent_cache)


# --------------------------------------------------------------------------- #
# Résolution depuis une référence (name) — route utils/compile/yaml-inline
# --------------------------------------------------------------------------- #

def resolve_reference(ref: str, kind: Optional[str] = None) -> Dict[str, Any]:
    """Charge un fichier du catalogue par name et retourne son inline."""
    # 1) par type explicite ou auto
    for k, folder in (("skill", "skills"), ("agent", "agents"),
                      ("team", "teams")):
        if kind and kind != k:
            continue
        root = _CATALOGUE / folder
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.yaml")):
            if ".old" in p.name or "inline." in p.name:
                continue
            data = _load_yaml_file(p)
            if data and data.get("name") == ref:
                return inline_document(data, kind=k)
    return {}
