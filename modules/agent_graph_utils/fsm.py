"""fsm.py — yaml_to_fsm : construit le graphe FSM hiérarchique d'un agent YAML.

Logique PURE (aucun thème, aucune position) : une liste de nœuds
{id, type, label, ref, tags, vars[inner]} et d'arêtes {from, to, label, type}.

HÉRITAGE DE TAGS :
  Les tags sont catégorisés en HÉRITABLES et NON héritables.
  - Un nœud porte ses tags PROPRES + les tags HÉRITABLES de ses ancêtres.
  - Seuls les tags HÉRITABLES descendent dans le sous-graphe (inner).
  Catégories héritables (pour l'instant) : heavy_tools, llm, sandbox,
  waiting, signal, tok_in, tok_out.

Structure :
  - chaque step → un nœud externe (skill / llm / flow / step / exitpoint…)
  - les composants STRUCTURELS (while/for, switch/if) ont un sous-graphe dans
    vars.inner (condition, body…) avec entrypoint/exitpoints.
  - nœud "main" (entrypoint) + arête vers le 1er step.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SubGraph = Dict[str, Any]

# Tags qui se propagent aux descendants (aujourd'hui).
HERITABLE_TAGS = {"heavy_tools", "llm", "sandbox", "waiting", "signal", "tok_in", "tok_out"}


def _kind(s: Dict[str, Any]) -> str:
    t = s.get("type")
    if t in ("switch", "if"):
        return t
    if t in ("while", "for"):
        return "loop"
    if t == "call" and s.get("fn"):
        return "skill"
    return "step"


def _step_type(s: Dict[str, Any]) -> str:
    t = s.get("type")
    if t == "end":
        return "exit_error" if s.get("status") == "FAILED" else "exitpoint"
    if t == "llm_call":
        return "llm"
    if t == "call":
        return "skill" if s.get("fn") else "fonction"
    if t in ("set_variable", "break", "continue"):
        return "step"
    return "fonction" if s.get("fn") else "step"


def _outer_type(s: Dict[str, Any]) -> str:
    if _kind(s) in ("loop", "switch", "if"):
        return "flow"
    return _step_type(s)


def _own_tags(s: Dict[str, Any]) -> List[str]:
    """Tags PROPRES d'un step (avant héritage)."""
    tags: List[str] = []
    t = s.get("type")
    fn = s.get("fn", "")
    if t == "llm_call":
        tags.append("llm")
    # tokens
    if "token_task_pick" in fn or "task_claim" in fn:
        tags.append("tok_in")
    if ("token_task_create" in fn or "end_exec" in fn or "task_done" in fn
            or "task_verdict" in fn or "exit_loop_too_hard" in fn
            or "token_task_release" in fn):
        tags.append("tok_out")
    if "wait_for" in fn:
        tags.append("waiting")
    # tags EXPLICITES déclarés dans le yaml (heavy_tools, sandbox, signal…)
    for tag in s.get("tags", []) or []:
        tags.append(tag)
    return list(dict.fromkeys(tags))


def _heritable(tags: List[str]) -> List[str]:
    """Ne garde que les tags HÉRITABLES."""
    return list(dict.fromkeys(t for t in tags if t in HERITABLE_TAGS))


def _build_body(steps: List[Dict[str, Any]], prefix: str,
                inherited: List[str]) -> SubGraph:
    """Sous-graphe d'une liste de steps. ``inherited`` = tags HÉRITABLES
    provenant des ancêtres."""
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    entrypoint: Optional[str] = None
    exitpoints: List[str] = []

    def resolve(ref: Optional[str]) -> Optional[str]:
        return f"{prefix}{ref}" if ref else None

    for s in steps:
        sid = f"{prefix}{s.get('id', '')}"
        own = _own_tags(s)
        node_tags = list(dict.fromkeys(inherited + own))
        node = {
            "id": sid,
            "type": _outer_type(s),
            "label": s.get("id", ""),
            "ref": s.get("fn") or s.get("type") or _kind(s),
            "tags": node_tags,
            "vars": {"inner": _build_inner(s, sid, list(dict.fromkeys(inherited + _heritable(own))))},
        }
        # 1er step top-level = ENTRYPOINT (tag hérité).
        if entrypoint is None and prefix == "" and "entrypoint" not in node_tags:
            node_tags = node_tags + ["entrypoint"]
            node["tags"] = node_tags
        nodes.append(node)
        if entrypoint is None:
            entrypoint = sid
        if s.get("type") in ("end", "break"):
            exitpoints.append(sid)

        # next / on_error
        nxt = resolve(s.get("next"))
        if nxt:
            edges.append({"from": sid, "to": nxt, "label": "next", "type": "next"})
        oe = resolve(s.get("on_error"))
        if oe:
            edges.append({"from": sid, "to": oe, "label": "err", "type": "error"})
        # switch/if : les branches sortent de la BOX au niveau parent.
        if s.get("type") in ("switch", "if"):
            seen = set()
            for c in s.get("conditions", []) or []:
                tgt = resolve(c.get("next"))
                if tgt and tgt not in seen:
                    seen.add(tgt)
                    var = s.get("variable", "?")
                    edges.append({"from": sid, "to": tgt,
                                 "label": f"{var}{c.get('operator', '')}{c.get('value', '')}",
                                 "type": "next"})
            dflt = resolve(s.get("default"))
            if dflt and dflt not in seen:
                edges.append({"from": sid, "to": dflt, "label": "else", "type": "next"})
        # continue → retourne à la condition de la boucle englobante.
        if s.get("type") == "continue":
            cond_id = prefix.replace("/body/", "/condition")
            edges.append({"from": sid, "to": cond_id, "label": "loop", "type": "loop"})

    return {"nodes": nodes, "edges": edges, "entrypoint": entrypoint,
            "exitpoints": exitpoints, "tags": inherited}


def _build_inner(s: Dict[str, Any], sid: str, inherited: List[str]) -> SubGraph:
    """Sous-graphe dépliable d'un step structurel (loop / switch / if)."""
    kind = _kind(s)
    if kind in ("switch", "if"):
        cond_id = f"{sid}/condition"
        return {
            "nodes": [{"id": cond_id, "type": "condition", "label": s.get("variable") or s.get("id", ""),
                       "ref": s.get("variable") or "switch",
                       "tags": ["entrypoint"], "vars": {}}],
            "edges": [], "entrypoint": cond_id, "exitpoints": [cond_id], "tags": ["condition"],
        }
    if kind == "loop":
        cond_id = f"{sid}/condition"
        tags = list(dict.fromkeys(inherited + ["loop", "entrypoint", "exitpoint"]))
        nodes: List[Dict[str, Any]] = [{
            "id": cond_id, "type": "condition", "label": f"loop {s.get('id', '')}",
            "ref": str(s.get("condition") or "while"), "tags": tags, "vars": {},
        }]
        edges: List[Dict[str, Any]] = []
        body_prefix = f"{sid}/body/"
        body = _build_body(s.get("body", {}).get("steps", []) or [], body_prefix, inherited)
        nodes.extend(body["nodes"])
        edges.extend(body["edges"])
        body_entry = body["entrypoint"]
        if body_entry:
            edges.append({"from": cond_id, "to": body_entry, "label": "true", "type": "loop"})
        # sortie "false" → les exitpoints (condition + breaks) au niveau parent.
        exitpoints = [cond_id] + body["exitpoints"]
        return {"nodes": nodes, "edges": edges, "entrypoint": cond_id,
                "exitpoints": exitpoints, "tags": inherited + ["loop"]}
    # call avec skill interne / steps simples : sous-graphe vide.
    if kind == "skill":
        if s.get("inner"):
            return _build_inner(s["inner"], sid, inherited)
        # SKILL : nœud interne dépliable [sid/skill] (entrypoint) — comme le
        # skillSubGraph historique. Permet à la GUI de déplier la skill.
        skill_id = f"{sid}/skill"
        return {
            "nodes": [{"id": skill_id, "type": "skill",
                       "label": s.get("fn") or s.get("id", ""),
                       "ref": s.get("fn") or "",
                       "tags": ["entrypoint"], "vars": {}}],
            "edges": [], "entrypoint": skill_id, "exitpoints": [skill_id],
            "tags": ["skill"],
        }
    return {"nodes": [], "edges": [], "entrypoint": None, "exitpoints": [], "tags": []}


def yaml_to_fsm(data: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Construit le graphe FSM hiérarchique d'un agent.

    ``data`` : dict parsé du .agent.yaml (role, entrypoints.main.steps).
    Retourne {"nodes": [...], "edges": [...], "title": ...}.
    """
    steps = (data or {}).get("entrypoints", {}).get("main", {}).get("steps", []) or []
    body = _build_body(steps, prefix="", inherited=[])
    nodes: List[Dict[str, Any]] = [{
        "id": "main", "type": "entrypoint", "label": "main", "ref": "entry",
        "tags": ["entrypoint"], "vars": {},
    }] + body["nodes"]
    edges: List[Dict[str, Any]] = []
    if steps:
        edges.append({"from": "main", "to": steps[0].get("id", ""), "label": "entry", "type": "next"})
    edges.extend(body["edges"])
    return {"nodes": nodes, "edges": edges, "title": (data or {}).get("name", "agent")}
