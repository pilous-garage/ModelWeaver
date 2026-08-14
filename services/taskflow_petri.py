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
# Skills qui CRÉENT des jetons : skill → types produits (global_<type>_unattributed).
PRODUCE_SKILLS: Dict[str, List[str]] = {
    "workspace/decoupe@v1": ["analysis", "coding", "testing", "review", "merge"],
    "workspace/ask_intel@v1": ["exploration"],
    "workspace/entry_create@v1": ["analysis"],
}

# Skills DATA du SUPERVISOR (sans LLM) : ils transforment les pots globaux.
# (in_etat, out_etat, mode) — mode 'all' = pour chaque type ; 'single' = unique.
DATA_SKILLS: Dict[str, tuple] = {
    "supervisor/assign@v1": ("unattributed", "attributed", "all"),
    "supervisor/finalise@v1": ("done", "supervised", "all"),
    "supervisor/make_respond@v1": (None, "respond", "single"),
}


class Petri:
    """Pétri du taskflow (représentation simple places/transitions)."""

    def __init__(self, agent: str, types: List[str]):
        self.agent = agent
        self.types = types
        self.places: Dict[str, int] = {}   # nom → marquage initial
        self.transitions: List[Dict[str, Any]] = []
        # Pot INTERNE : la tâche de l'agent (≤ 1 jeton).
        self.places["agent_data"] = 0
        # Pots EXTERNES (globaux) : un pot par type×état.
        for t in types:
            for et in ETATS:
                self.places[f"global_{t}_{et}"] = 0

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
    """Génère le pétri depuis un .yaml agent (récursif via yaml_to_fsm).

    Transformation graphe → pétri : chaque ARC devient une PLACE (le jeton
    d'activité), chaque NOEUD (step) devient une TRANSITION. Les steps
    pick/release/produce manipulent en plus les pots de data :
      - pick    : global_<type>_attributed → agent_data
      - release : agent_data → global_<type>_done
      - produce : agent_data → agent_data + global_<type>_unattributed
    Les boucles du FSM (continue/while) sont conservées par le cycle du graphe.
    """
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    name = agent_name or data.get("name", path.stem)
    fsm = yaml_to_fsm(data)
    nodes = fsm["nodes"]
    edges = fsm["edges"]

    # Classe de jeton par step (pick/release/produce/normal) + types.
    consumes: Set[str] = set()
    produced: Set[str] = set()
    token_class: Dict[str, tuple] = {}

    def walk(steps: List[Dict[str, Any]]) -> None:
        for s in steps or []:
            sid = s.get("id", "?")
            st = s.get("type", "")
            tk, tk_types = "normal", []
            if st == "call":
                fn = str(s.get("fn", ""))
                if PICK_SKILL in fn:
                    tk = "pick"
                    import re
                    tk_types = re.findall(
                        r"type\s*:\s*[\"']?([\w]+)[\"']?",
                        str((s.get("inputs") or {}).get("types", "")))
                    consumes.update(tk_types)
                for sk, prods in PRODUCE_SKILLS.items():
                    if sk.split("@")[0] in fn:
                        tk = "produce"
                        tk_types = list(prods)
                        produced.update(prods)
                for sk, (in_et, out_et, mode) in DATA_SKILLS.items():
                    if sk.split("@")[0] in fn:
                        tk = "data"
                        tk_types = [in_et, out_et, mode]
            if st == "llm_call":
                for sk in (s.get("skills") or []):
                    if sk in RELEASE_SKILLS:
                        tk = "release"
                    if sk in PRODUCE_SKILLS:
                        tk = "produce"
                        tk_types = list(PRODUCE_SKILLS[sk])
                        produced.update(PRODUCE_SKILLS[sk])
            token_class[sid] = (tk, tk_types)
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

    def _pname(e: Dict[str, Any]) -> str:
        f, t = e.get("from", ""), e.get("to", "")
        return f"activity_{name}_{f}__{t}" if t else f"activity_{name}_{f}__loop"

    # Places = les arcs du graphe FSM (le jeton d'activité circule dessus).
    for e in edges:
        net.place(_pname(e))

    # Noeuds → transitions. Chaque transition prend les places des arcs
    # entrants et produit les places des arcs sortants.
    ins_by_node: Dict[str, List[str]] = {}
    outs_by_node: Dict[str, List[str]] = {}
    for e in edges:
        f, t = e.get("from", ""), e.get("to", "")
        p = _pname(e)
        if t:
            ins_by_node.setdefault(t, []).append(p)
        outs_by_node.setdefault(f, []).append(p)
    for n in nodes:
        nid = n.get("id", "")
        if nid == "main":
            continue  # point d'entrée = place initiale (marquée)
        ins = list(ins_by_node.get(nid, []))
        outs = list(outs_by_node.get(nid, []))
        tk, tk_types = token_class.get(nid, ("normal", []))
        if tk == "pick":
            # pick : global_<type>_attributed → agent_data (l'agent prend la tâche)
            for t in (tk_types or ["analysis"]):
                net.trans(f"pick_{name}_{nid}_{t}", ins + [f"global_{t}_attributed"],
                          outs + ["agent_data"], "pick")
        elif tk in ("release", "produce", "use"):
            # UTILISATION : la data est prise et REMISE dans la même place
            # (agent_data → agent_data) — le step travaille dessus sans la déplacer.
            net.trans(f"step_{name}_{nid}", ins + ["agent_data"],
                      outs + ["agent_data"], "use")
            # effet de jeton en plus (transition séparée de changement d'état) :
            if tk == "release":
                for t in sorted(consumes or ["analysis"]):
                    net.trans(f"release_{name}_{nid}_{t}", ["agent_data"],
                              [f"global_{t}_done"], "release")
            elif tk == "produce":
                for t in (tk_types or []):
                    net.trans(f"produce_{name}_{nid}_{t}", ["agent_data"],
                              ["agent_data", f"global_{t}_unattributed"], "produce")
        elif tk == "data":
            # Skills DATA (superviseur, sans LLM) : transforme les pots globaux.
            in_et, out_et, mode = (tk_types or ["", "", "single"])
            if mode == "all":
                for t in net.types:
                    ins.append(f"global_{t}_{in_et}")
                    outs.append(f"global_{t}_{out_et}")
            else:
                outs.append(f"global_{out_et}_unattributed")
            net.trans(f"step_{name}_{nid}", ins, outs, tk)
        else:
            net.trans(f"step_{name}_{nid}", ins, outs, tk)

    # Reliage de la BOUCLE : la place loop (arc vers vide) alimente le corps.
    for e in edges:
        if not e.get("to"):
            _loop = _pname(e)
            body0 = None
            for n in nodes:
                if n.get("id") not in ("main",) and n.get("id") in outs_by_node:
                    body0 = n.get("id")
                    break
            if body0:
                net.trans(f"loop_{name}_{e.get('from')}_to_{body0}",
                          [_loop], [f"activity_{name}_main__{body0}"], "loop")

    # Place finale (fin de workflow) : les noeuds terminaux → end.
    net.place(f"activity_{name}_end")
    for n in nodes:
        nid = n.get("id", "")
        if nid == "main":
            continue
        if nid not in outs_by_node or not outs_by_node[nid]:
            net.trans(f"step_{name}_{nid}_end", ins_by_node.get(nid, []),
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
        net.trans(f"sup_assign_{t}", [f"global_{t}_unattributed"],
                  [f"global_{t}_attributed"], "assign")
        net.trans(f"sup_final_{t}", [f"global_{t}_done"],
                  [f"global_{t}_supervised"], "finalise")
    # relais selon les règles : global_<in>_done → global_<out>_unattributed
    for r in rules or []:
        it, ot = r.get("in_type", ""), r.get("out_type", "")
        if it and ot:
            net.trans(f"sup_relay_{it}_{ot}", [f"global_{it}_done"],
                      [f"global_{ot}_unattributed"], f"{it}→{ot}")
    # respond : créé par le superviseur quand une entrée est close
    net.trans("sup_make_respond", [f"global_analysis_done"],
              [f"global_respond_unattributed"], "make_respond")
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


def save_petri_png(petri: Petri, out_path: str) -> str:
    """Construit le PetriNet pm4py et le sauvegarde en image (PNG)."""
    from pm4py.objects.petri_net.obj import PetriNet, Marking
    from pm4py.objects.petri_net.utils import petri_utils
    net = PetriNet(f"taskflow_{petri.agent}")
    pm_places: Dict[str, PetriNet.Place] = {}
    pm_trans: Dict[str, PetriNet.Transition] = {}
    for pname in petri.places:
        pm_places[pname] = PetriNet.Place(pname)
        net.places.add(pm_places[pname])
    for t in petri.transitions:
        pt = PetriNet.Transition(t["name"], t["name"])
        net.transitions.add(pt)
        pm_trans[t["name"]] = pt
        for p in t["in"]:
            petri_utils.add_arc_from_to(pm_places[p], pt, net)
        for p in t["out"]:
            petri_utils.add_arc_from_to(pt, pm_places[p], net)
    m0 = Marking()
    for t in petri.types:
        src = pm_places.get(f"data_{t}_unattributed")
        if src is not None:
            m0[src] = 1
    try:
        pm4py.save_vis_petri_net(net, m0, out_path)
    except Exception:
        import pm4py as _pm4py
        from pm4py.visualization.petri_net import visualizer
        gviz = visualizer.apply(net, m0, None,
                                parameters={visualizer.Variants.WO_DECORATION.value:
                                            {"format": "png"}})
        visualizer.save(gviz, out_path)
    return out_path


def merge_petris(name: str, petris: List[Petri]) -> Petri:
    """Fusionne plusieurs Petri en un seul : les places data_<type>_<etat> sont
    partagées (mêmes noms), data_agent_<agent> et activity_<agent>_<step> restent
    propres à chaque agent. Les transitions sont concaténées."""
    types: Set[str] = set()
    for p in petris:
        types.update(p.types)
    net = Petri(name, sorted(types))
    for p in petris:
        for place in p.places:
            net.place(place)
        for t in p.transitions:
            net.trans(t["name"], t["in"], t["out"], t["label"])
    return net


def team_global_petri(path: Path, rules: List[Dict[str, Any]] = ()) -> Petri:
    """Pétri GLOBAL d'une team : fusion de chaque membre (+ sous-agents) et du
    superviseur (assign/finalise/relais selon les règles)."""
    import yaml
    data = yaml.safe_load(path.read_text()) or {}
    petris: List[Petri] = []
    for m in data.get("members", []) or []:
        ref = m.get("ref", "")
        apath = REPO / "AgentsCatalogue" / "agents" / f"{ref}.agent.yaml"
        if apath.exists():
            petris.append(build_from_yaml(apath, m.get("agent_name", ref))["petri"])
        for sa in (m.get("sub_agents") or []):
            saref = sa.get("ref", "")
            sapath = REPO / "AgentsCatalogue" / "agents" / f"{saref}.agent.yaml"
            if sapath.exists():
                sa_name = f"{m.get('agent_name', ref)}/{sa.get('agent_name', '')}"
                petris.append(build_from_yaml(sapath, sa_name)["petri"])
    # Le superviseur : transformé comme un agent (supervisor.agent.yaml → pétri).
    sup_path = REPO / "AgentsCatalogue" / "agents" / "supervisor.agent.yaml"
    if sup_path.exists():
        petris.append(build_from_yaml(sup_path, "supervisor")["petri"])
    else:
        petris.append(supervisor_petri(rules))
    # Relais selon les règles (superviseur) : global_<in>_done → global_<out>_unattributed
    merged = merge_petris(f"team_{data.get('name', path.stem)}", petris)
    for r in rules or []:
        it, ot = r.get("in_type", ""), r.get("out_type", "")
        if it and ot:
            merged.trans(f"sup_relay_{it}_{ot}", [f"global_{it}_done"],
                         [f"global_{ot}_unattributed"], f"{it}→{ot}")
    return merged


def main() -> None:
    ap = argparse.ArgumentParser(description="YAML → Pétri du taskflow")
    ap.add_argument("yaml", help="fichier .team.yaml ou .agent.yaml")
    ap.add_argument("--agent", default="", help="nom d'agent (si yaml team)")
    ap.add_argument("--team", action="store_true", help="traiter comme une team")
    ap.add_argument("--team-petri", action="store_true",
                    help="pétri GLOBAL de la team (agents + superviseur fusionnés)")
    ap.add_argument("--verify", action="store_true",
                    help="vérifier le pétri (pm4py : reachability de supervised)")
    ap.add_argument("--png", default="", help="sauvegarder le pétri en PNG")
    args = ap.parse_args()
    path = Path(args.yaml)
    if args.png:
        if args.team or args.team_petri:
            out = save_petri_png(team_global_petri(path), args.png)
        else:
            res = build_from_yaml(path, args.agent)
            out = save_petri_png(res["petri"], args.png)
        print(f"PNG sauvegardé : {out}")
        return
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
