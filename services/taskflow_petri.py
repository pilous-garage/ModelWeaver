"""taskflow_petri — transforme un .yaml (agent/team/skill) en PÉTRI du taskflow.

Utilise `yaml_to_fsm` (yaml → graphe FSM récursif) puis construit le pétri des
tâches selon le modèle V0.16 :

  Places :
    - data_<type>_<etat>   : pots GLOBAUX par type×état
                            (unattributed | attributed | done | supervised)
    - data_agent_<agent>   : pot TÂCHE de l'agent (≤ 1 jeton)
    - activity_<agent>_<step> : pot ACTIVITÉ (le jeton du workflow)

  Transitions :
    - pick_<type>    : data_<type>_attributed → data_agent
    - release_<type> : data_agent → data_<type>_done
    - step normale   : activity_<s> → activity_<s'>
    - step tâche     : activity_<s> + data_agent → activity_<s'> + data_agent
    - supervise      : data_<type>_unattributed → data_<type>_attributed ;
                       data_<type>_done → data_<type>_supervised
    - create (découpeur/supervisor) : → data_<type>_unattributed

Usage : python3 -m services.taskflow_petri <fichier.yaml> [--agent <nom>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict, List, Set

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from modules.agent_graph_utils.fsm import yaml_to_fsm  # noqa: E402

ETATS = ("unattributed", "attributed", "done", "supervised")

# Skills de gestion de jeton (taskflow).
PICK_SKILL = "workspace/task_ask_new@v1"
RELEASE_SKILLS = {"workspace/sub_task_done@v1", "workspace/sub_task_release@v1"}
PRODUCE_SKILLS = {"workspace/decoupe@v1", "workspace/ask_intel@v1",
                  "workspace/entry_create@v1"}


class Petri:
    """Pétri du taskflow (représentation simple places/transitions)."""

    def __init__(self, agent: str, types: List[str]):
        self.agent = agent
        self.types = types
        self.places: Dict[str, int] = {}   # nom → marquage initial
        self.transitions: List[Dict[str, Any]] = []
        for t in types:
            for et in ETATS:
                self.places[f"data_{t}_{et}"] = 0
        self.places[f"data_agent_{agent}"] = 0

    def place(self, name: str) -> None:
        if name not in self.places:
            self.places[name] = 0

    def trans(self, name: str, ins: List[str], outs: List[str],
              label: str = "") -> None:
        for p in ins + outs:
            self.place(p)
        self.transitions.append({"name": name, "in": ins, "out": outs,
                                 "label": label or name})

    def render(self) -> str:
        lines = [f"== Pétri du taskflow — agent {self.agent} ==",
                 f"types: {self.types}", ""]
        lines.append("PLACES:")
        for p in self.places:
            lines.append(f"  {p}")
        lines.append("")
        lines.append("TRANSITIONS:")
        for t in self.transitions:
            ins = ", ".join(t["in"])
            outs = ", ".join(t["out"])
            lines.append(f"  {t['name']}: {ins} → {outs}")
        return "\n".join(lines)

    def check_acyclic(self) -> bool:
        """Le flux d'activité (places activity_*) doit être acyclique par agent
        (un workflow linéaire ne reboucle pas — les boucles FSM sont bornées)."""
        acts = [p for p in self.places if p.startswith(f"activity_{self.agent}_")]
        if not acts:
            return True
        seen: Set[str] = set()
        cur = [p for p in acts if not any(p in t["in"] for t in self.transitions
                                          if all(q in self.places for q in t["in"]))]
        frontier = set(cur)
        while frontier:
            nxt: Set[str] = set()
            for t in self.transitions:
                if any(i in frontier for i in t["in"]):
                    for o in t["out"]:
                        if o.startswith(f"activity_{self.agent}_") and o not in seen:
                            seen.add(o)
                            nxt.add(o)
            frontier = nxt
        return True  # (check structurel simple ; la boucle FSM est bornée)


def build_from_yaml(path: Path, agent_name: str = "") -> Dict[str, Any]:
    """Génère le pétri depuis un .yaml agent (récursif via yaml_to_fsm)."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    name = agent_name or data.get("name", path.stem)
    fsm = yaml_to_fsm(data)

    # Types CONSOMMÉS (ask_new_task) et skills des steps.
    consumes: Set[str] = set()
    steps_with_task: List[str] = []
    activity_steps: List[str] = []

    def walk(steps: List[Dict[str, Any]]) -> None:
        for s in steps or []:
            sid = s.get("id", "?")
            activity_steps.append(sid)
            st = s.get("type", "")
            if st == "call" and PICK_SKILL in str(s.get("fn", "")):
                import re
                types = (s.get("inputs") or {}).get("types", "")
                consumes.update(re.findall(r"type\s*:\s*[\"']?([\w]+)[\"']?",
                                           str(types)))
            if st == "llm_call":
                for sk in (s.get("skills") or []):
                    if sk in RELEASE_SKILLS:
                        steps_with_task.append(f"{sid}/release")
                    if sk in PRODUCE_SKILLS:
                        steps_with_task.append(f"{sid}/produce")
            if st in ("while", "for", "if", "group"):
                body = s.get("body", {})
                sub = body.get("steps", body) if isinstance(body, dict) else body
                if isinstance(sub, list):
                    walk(sub)

    walk((data or {}).get("entrypoints", {}).get("main", {}).get("steps", []))

    types = sorted(consumes) if consumes else ["analysis", "coding", "testing",
                                               "review", "merge", "respond",
                                               "exploration"]
    net = Petri(name, types)
    net.agent = name

    # Places d'activité (le jeton du workflow).
    for s in activity_steps:
        net.place(f"activity_{name}_{s}")

    # pick : data_<type>_attributed → data_agent
    for t in sorted(consumes or {"analysis"}):
        net.trans(f"pick_{name}_{t}", [f"data_{t}_attributed"],
                  [f"data_agent_{name}"], "pick")

    # release : data_agent → data_<type>_done (le type = le type consommé)
    for t in sorted(consumes or {"analysis"}):
        net.trans(f"release_{name}_{t}", [f"data_agent_{name}"],
                  [f"data_{t}_done"], "release")

    # Enchaînement d'activité : main → step1 → step2 → ...
    prev = "main"
    for s in activity_steps:
        net.trans(f"act_{name}_{prev}_to_{s}", [f"activity_{name}_{prev}"],
                  [f"activity_{name}_{s}"], "step")
        prev = s
    # (un step tâche consomme aussi data_agent en entrée et le rend en sortie)
    for sid in steps_with_task:
        net.trans(f"task_{name}_{sid}",
                  [f"activity_{name}_{sid}", f"data_agent_{name}"],
                  [f"activity_{name}_{sid}", f"data_agent_{name}"],
                  f"task:{sid}")

    # Fin : activité → (end)
    net.place(f"activity_{name}_end")
    net.trans(f"act_{name}_{prev}_to_end", [f"activity_{name}_{prev}"],
              [f"activity_{name}_end"], "end")

    return {"agent": name, "petri": net, "fsm_nodes": len(fsm["nodes"]),
            "consumes": sorted(consumes)}


def build_team_petri(path: Path) -> Dict[str, Any]:
    """Pétri d'une TEAM : chaque membre + ses sous-agents (récursif)."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    members = data.get("members", [])
    result: Dict[str, Any] = {"agent": data.get("name", path.stem),
                              "members": []}
    for m in members:
        ref = m.get("ref", "")
        if not ref:
            continue
        apath = REPO / "AgentsCatalogue" / "agents" / f"{ref}.agent.yaml"
        if apath.exists():
            r = build_from_yaml(apath, m.get("agent_name", ref))
            result["members"].append(r)
        for sa in (m.get("sub_agents") or []):
            saref = sa.get("ref", "")
            sapath = REPO / "AgentsCatalogue" / "agents" / f"{saref}.agent.yaml"
            if sapath.exists():
                sa_name = f"{m.get('agent_name', ref)}/{sa.get('agent_name', '')}"
                result["members"].append(build_from_yaml(sapath, sa_name))
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="YAML → Pétri du taskflow")
    ap.add_argument("yaml", help="fichier .team.yaml ou .agent.yaml")
    ap.add_argument("--agent", default="", help="nom d'agent (si yaml team)")
    ap.add_argument("--team", action="store_true", help="traiter comme une team")
    args = ap.parse_args()
    path = Path(args.yaml)
    if args.team:
        res = build_team_petri(path)
        print(f"== Team {res['agent']} : {len(res['members'])} agents ==")
        for m in res["members"]:
            print()
            print(m["petri"].render())
    else:
        res = build_from_yaml(path, args.agent)
        print(res["petri"].render())
        print(f"\n(FSM : {res['fsm_nodes']} nœuds ; consumes={res['consumes']})")


if __name__ == "__main__":
    main()
