"""taskflow_diag — diagnostic du pétri des tâches (in/out par agent).

Pétri standardisé (V0.16) :
- Place TÂCHE : le jeton (sub_task) que l'agent traite (1 par agent, `doing`).
- Place ACTIVITÉ : la step/skill de plus bas niveau en cours.
- Transitions :
    consommer : picker/ask_new_task (pool externe → doing) ;
    remettre  : sub_task_done/release (+tag) (doing → done / retour flux) ;
    produire  : decoupe / ask_intel / entry_create / supervisor (nouveaux jetons).

`task_in_out(agent)` retourne les types que l'agent CONSOMME (au pool) et les
types qu'il PRODUIT (crée) + son activité (steps/skills).

Côté agent (hors supervisor) : uniquement pick (attributed→doing) et
release (doing→done/cancelled). Le supervisor gère unattributed→attributed et
done/cancelled→supervised ; le découpeur crée les sous-tâches.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

# Carte skill → types de sub_tasks PRODUITS (créés) par le skill.
SKILL_PRODUCES: Dict[str, List[str]] = {
    "workspace/decoupe@v1": ["planning", "coding", "testing", "reviewing", "merging"],
    "workspace/ask_intel@v1": ["exploration"],
    "workspace/entry_create@v1": ["planning"],
}

# Skills qui REMETTENT le jeton (l'utilisent sans créer) — pas des productions.
RELEASE_SKILLS = {"workspace/sub_task_done@v1", "workspace/sub_task_release@v1"}


def _load_agent_config(agent: str) -> Dict[str, Any]:
    """Charge le config de l'agent (yaml catalogue, sinon BDD)."""
    p = Path(__file__).resolve().parent.parent / "AgentsCatalogue" / "agents"
    for fname in (f"{agent}.agent.yaml", f"{agent}.yaml"):
        f = p / fname
        if f.exists():
            import yaml
            return yaml.safe_load(f.read_text()) or {}
    try:
        from modules.sql.db import AgentsDB
        db = AgentsDB()
        row = db.conn.execute(
            "SELECT config_json FROM agents WHERE name LIKE ? OR ref LIKE ?",
            (f"%{agent}%", f"%{agent}%")).fetchone()
        db.close()
        if row:
            import json
            return json.loads(row["config_json"] or "{}")
    except Exception:
        pass
    return {}


def _parse_types(types_raw: Any) -> List[str]:
    """Extrait les types d'un ask_new_task (string yaml ou liste)."""
    if isinstance(types_raw, list):
        out = []
        for t in types_raw:
            if isinstance(t, dict):
                out.append(str(t.get("type", "")))
            else:
                out.append(str(t))
        return [x for x in out if x]
    if isinstance(types_raw, str):
        import re
        return re.findall(r"type\s*:\s*[\"']?([\w]+)[\"']?", types_raw)
    return []


def _walk_steps(steps: List[Dict[str, Any]], out: Dict[str, Any]) -> None:
    """Parcourt les steps (et les corps de boucles) : collecte consumes/produces/activité."""
    for s in steps or []:
        stype = s.get("type", "")
        sid = s.get("id", "?")
        if sid not in out["activity_steps"]:
            out["activity_steps"].append(sid)
        if stype == "call":
            fn = str(s.get("fn", ""))
            if "task_ask_new" in fn:
                out["consumes"].extend(_parse_types((s.get("inputs") or {}).get("types")))
            for sk, prods in SKILL_PRODUCES.items():
                if sk in fn:
                    out["produces"].extend(prods)
        if stype == "llm_call":
            for sk in (s.get("skills") or []):
                prod = SKILL_PRODUCES.get(sk)
                if prod:
                    out["produces"].extend(prod)
                if sk not in out["activity_skills"]:
                    out["activity_skills"].append(sk)
        if stype in ("while", "for", "if", "group"):
            body = s.get("body", {})
            sub = body.get("steps", body) if isinstance(body, dict) else body
            if isinstance(sub, list):
                _walk_steps(sub, out)


def task_in_out(agent: str) -> Dict[str, Any]:
    """Pétri d'un agent : consumes (types pris au pool), produces (types créés),
    activity (steps/skills de son workflow)."""
    cfg = _load_agent_config(agent)
    out: Dict[str, Any] = {
        "agent": agent,
        "consumes": [],
        "produces": [],
        "activity_steps": [],
        "activity_skills": [],
    }
    entrypoints = cfg.get("entrypoints", {}) or {}
    for name, wf in entrypoints.items():
        if isinstance(wf, dict):
            _walk_steps(wf.get("steps", []), out)
    # Déduplication, ordre stable
    out["consumes"] = sorted(set(out["consumes"]))
    out["produces"] = sorted(set(out["produces"]))
    return out


def supervisor_in_out(rules: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Pétri du SUPERVISOR (sans LLM) : consomme toutes les sub_tasks à
    superviser, produit les relais (règles) + respond."""
    produced = {"respond"}
    for r in rules or []:
        ot = r.get("out_type") or ""
        if ot:
            produced.add(ot)
    return {
        "agent": "task_supervisor",
        "consumes": ["*"],
        "produces": sorted(produced),
        "activity_steps": ["supervise", "assign", "release_dependencies"],
        "activity_skills": [],
    }


__all__ = ["task_in_out", "supervisor_in_out", "SKILL_PRODUCES"]
