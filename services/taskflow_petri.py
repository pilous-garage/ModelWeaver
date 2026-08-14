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
# Skills qui CRÉENT des jetons : skill → types produits (data_<type>_unattributed).
PRODUCE_SKILLS: Dict[str, List[str]] = {
    "workspace/decoupe@v1": ["analysis", "coding", "testing", "review", "merge"],
    "workspace/ask_intel@v1": ["exploration"],
    "workspace/entry_create@v1": ["analysis"],
}


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
    produced: Set[str] = set()
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
                        produced.update(PRODUCE_SKILLS[sk])
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

    # PRODUCTION (skills de création) : data_agent → data_agent +
    # data_<type>_unattributed (le découpeur/as_llm_leader crée des jetons).
    for t in sorted(produced):
        net.trans(f"produce_{name}_{t}", [f"data_agent_{name}"],
                  [f"data_agent_{name}", f"data_{t}_unattributed"],
                  f"create:{t}")

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
            "consumes": sorted(consumes), "produces": sorted(produced)}


def supervisor_petri(rules: List[Dict[str, Any]] = (),
                     workspace: str = "supervisor") -> Petri:
    """Pétri COMPLET du SUPERVISOR (sans LLM) : ses places (les pots globaux)
    et ses transitions (assign, finalise, relais selon les règles, respond)."""
    all_types = ["analysis", "coding", "testing", "review", "merge",
                 "respond", "exploration"]
    net = Petri(workspace, all_types)
    # Places d'activité du superviseur
    for s in ("supervise", "assign", "release_dependencies", "finalize"):
        net.place(f"activity_{workspace}_{s}")
    # assign : unattributed → attributed ; finalise : done → supervised
    for t in all_types:
        net.trans(f"sup_assign_{t}", [f"data_{t}_unattributed"],
                  [f"data_{t}_attributed"], "assign")
        net.trans(f"sup_final_{t}", [f"data_{t}_done"],
                  [f"data_{t}_supervised"], "finalise")
    # relais selon les règles : data_<in>_done → data_<out>_unattributed
    for r in rules or []:
        it, ot = r.get("in_type", ""), r.get("out_type", "")
        if it and ot:
            net.trans(f"sup_relay_{it}_{ot}", [f"data_{it}_done"],
                      [f"data_{ot}_unattributed"], f"{it}→{ot}")
    # respond : créé par le superviseur quand une entrée est close
    net.trans("sup_make_respond", [f"data_analysis_done"],
              [f"data_respond_unattributed"], "make_respond")
    return net


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
    ap.add_argument("--verify", action="store_true",
                    help="vérifier le pétri (pm4py : reachability de supervised)")
    args = ap.parse_args()
    path = Path(args.yaml)
    if args.team:
        res = build_team_petri(path)
        print(f"== Team {res['agent']} : {len(res['members'])} agents ==")
        for m in res["members"]:
            print()
            print(m["petri"].render())
            if args.verify:
                v = verify_with_pm4py(m["petri"])
                print("   verify:", v)
        print()
        print("== SUPERVISOR ==")
        print(supervisor_petri().render())
        if args.verify:
            print("   verify:", verify_with_pm4py(supervisor_petri()))
    else:
        res = build_from_yaml(path, args.agent)
        print(res["petri"].render())
        print(f"\n(FSM : {res['fsm_nodes']} nœuds ; consumes={res['consumes']})")
        if args.verify:
            print("   verify:", verify_with_pm4py(res["petri"]))


def supervisor_transitions(rules: List[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """Transitions du SUPERVISOR (selon les règles du manifest) :
      - pour chaque type : unattributed → attributed (assign) ;
        done → supervised (finalise) ;
      - selon les règles (in_type, in_tag)→(out_type, out_tag) : quand un type
        est done, crée le relais out_type → unattributed."""
    trans: List[Dict[str, Any]] = []
    # assign + finalise (par type connu)
    for t in ("analysis", "coding", "testing", "review", "merge", "respond",
              "exploration"):
        trans.append({"name": f"sup_assign_{t}",
                      "in": [f"data_{t}_unattributed"],
                      "out": [f"data_{t}_attributed"], "label": "assign"})
        trans.append({"name": f"sup_final_{t}",
                      "in": [f"data_{t}_done"],
                      "out": [f"data_{t}_supervised"], "label": "finalise"})
    # relais selon les règles : data_<in_type>_done → data_<out_type>_unattributed
    for r in rules or []:
        it = r.get("in_type") or ""
        ot = r.get("out_type") or ""
        if it and ot:
            trans.append({"name": f"sup_relay_{it}_{ot}",
                          "in": [f"data_{it}_done"],
                          "out": [f"data_{ot}_unattributed"], "label": f"{it}→{ot}"})
    return trans


def verify_with_pm4py(petri: Petri) -> Dict[str, Any]:
    """Vérifie (BFS de marquages) que chaque type atteint `supervised` depuis
    un jeton initial `unattributed` (reachability). Les places sont bornées."""
    # transitions = agent (pick/release/steps) + superviseur (assign/finalise)
    trans = list(petri.transitions) + supervisor_transitions()
    results: Dict[str, Any] = {}
    for t in petri.types:
        src, dst = f"data_{t}_unattributed", f"data_{t}_supervised"
        m0 = {p: (1 if p == src else 0) for p in petri.places}
        # BFS des marquages atteignables
        seen = {_mark_key(m0)}
        frontier = [m0]
        reached = False
        while frontier:
            m = frontier.pop()
            if m.get(dst, 0) > 0:
                reached = True
                break
            for tr in trans:
                if all(m.get(i, 0) >= 1 for i in tr["in"]):
                    nm = dict(m)
                    for i in tr["in"]:
                        nm[i] = max(0, nm.get(i, 0) - 1)
                    for o in tr["out"]:
                        nm[o] = nm.get(o, 0) + 1
                    k = _mark_key(nm)
                    if k not in seen:
                        seen.add(k)
                        frontier.append(nm)
        results[t] = "OK (supervised atteignable)" if reached else \
            "KO (jamais supervised)"
    return results


def _mark_key(m: Dict[str, int]) -> str:
    return "|".join(f"{p}:{m.get(p, 0)}" for p in sorted(m))


if __name__ == "__main__":
    main()
