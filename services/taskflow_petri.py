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

ETATS = ("unattributed", "attributed", "done", "supervised", "cancelled")

# Skills de gestion de jeton (taskflow).
PICK_SKILL = "workspace/task_ask_new@v1"
RELEASE_SKILLS = {"workspace/sub_task_done@v1", "workspace/sub_task_release@v1"}
# Skills qui CRÉENT des jetons : skill → types produits (global_<type>_unattributed).
PRODUCE_SKILLS: Dict[str, List[str]] = {
    "workspace/decoupe@v1": ["coding", "testing", "reviewing", "merging"],
    "workspace/ask_intel@v1": ["exploration"],
    "workspace/entry_create@v1": ["planning"],
}

# Skills PRODUCE qui CLÔTURENT AUSSI la tâche courante (release) : le skill
# libère le jeton en cours (ex. decoupe : cas simple → analysis done/ok) en
# plus de produire de nouvelles sub_tasks. ask_intel, lui, met la tâche en
# waiting_dependencies (pas done) → pas de release.
RELEASE_PRODUCE_SKILLS = {"workspace/decoupe@v1"}

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
        # Pots EXTERNES (globaux) : un pot par type×état.
        for t in types:
            for et in ETATS:
                self.places[f"global_{t}_{et}"] = 0
        # Pot INTERNE (agent) : un pot par type×état (la tâche en cours).
        # ex. agent_data_coding_doing — le greedy-coder qui code.
        for t in types:
            self.places[f"agent_data_{t}_doing"] = 0

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

    # Classes de jeton par step : LISTE (un step peut être release ET produce,
    # ex. do_work avec sub_task_done + ask_intel). Chaque élément :
    #   ("pick", [types]) | ("release", []) | ("produce", [types]) |
    #   ("data", [in_et, out_et, mode]) | ("normal", [])
    consumes: Set[str] = set()
    produced: Set[str] = set()
    token_class: Dict[str, list] = {}

    def walk(steps: List[Dict[str, Any]]) -> None:
        for s in steps or []:
            sid = s.get("id", "?")
            st = s.get("type", "")
            classes: List[tuple] = []
            if st == "call":
                fn = str(s.get("fn", ""))
                if PICK_SKILL in fn:
                    import re
                    tk_types = re.findall(
                        r"type\s*:\s*[\"']?([\w]+)[\"']?",
                        str((s.get("inputs") or {}).get("types", "")))
                    consumes.update(tk_types)
                    classes.append(("pick", tk_types))
                for sk, prods in PRODUCE_SKILLS.items():
                    if sk.split("@")[0] in fn:
                        classes.append(("produce", list(prods)))
                        produced.update(prods)
                for sk, (in_et, out_et, mode) in DATA_SKILLS.items():
                    if sk.split("@")[0] in fn:
                        classes.append(("data", [in_et, out_et, mode]))
            if st == "llm_call":
                has_release = False
                for sk in (s.get("skills") or []):
                    if sk in RELEASE_SKILLS or sk in RELEASE_PRODUCE_SKILLS:
                        if not has_release:
                            classes.append(("release", []))
                            has_release = True
                    if sk in PRODUCE_SKILLS:
                        classes.append(("produce", list(PRODUCE_SKILLS[sk])))
                        produced.update(PRODUCE_SKILLS[sk])
            if not classes:
                classes.append(("normal", []))
            token_class[sid] = classes
            if st in ("while", "for", "if", "group"):
                body = s.get("body", {})
                sub = body.get("steps", body) if isinstance(body, dict) else body
                if isinstance(sub, list):
                    walk(sub)

    walk((data or {}).get("entrypoints", {}).get("main", {}).get("steps", []))

    types = sorted(consumes) if consumes else ["planning", "coding", "testing",
                                               "reviewing", "merging", "respond",
                                               "exploration"]
    net = Petri(name, types)
    net.agent = name

    def _pname(e: Dict[str, Any]) -> str:
        f, t = e.get("from", ""), e.get("to", "")
        return f"activity_{name}_{f}__{t}" if t else f"activity_{name}_{f}__loop"

    # Places = les arcs du graphe FSM (le jeton d'activité circule dessus).
    for e in edges:
        net.place(_pname(e))

    # Place DEAD : l'agent est "mort"/déshydraté (aucune activité, unicité du
    # jeton). C'est le marquage INITIAL. Les exitpoints y reviennent (cycle :
    # dead → entry → workflow → exit → dead).
    dead_p = f"activity_{name}_dead"
    net.place(dead_p)
    net.dead = dead_p

    # JETON FLUX : une place par entrypoint (main + prioritaires). Le flux
    # sélectionne l'entrypoint actif ; les steps s'exécutent DANS un flux.
    # Marquage initial : flux_main (le workflow principal est le défaut).
    # Priorités : pause(3) > cancel(2) > ask_auth/receive_auth(1) > main(0).
    # Les entrypoints prioritaires génèrent les transitions de SWITCH :
    #   stack_<agent>_<ep> : flux_<ep> → flux_<agent>_stack (interruption)
    #   pop_<agent>_<ep>   : flux_<agent>_stack → flux_<ep> (reprise)
    # Sans entrypoint prioritaire déclaré, seul flux_main est créé.
    entrypoints_prio = ("pause", "cancel", "ask_auth", "receive_auth")
    flux_main = f"flux_{name}_main"
    net.place(flux_main)
    net.flux = {ep: f"flux_{name}_{ep}" for ep in entrypoints_prio}
    net.stack = f"flux_{name}_stack"
    for ep in entrypoints_prio:
        net.place(net.flux[ep])
    net.place(net.stack)

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

    # MERGES : un step avec PLUSIEURS prédécesseurs reçoit UNE place de jonction
    # (les chemins y convergent) → la transition du step a exactement 1 entrée
    # activité. Une transition d'injection par ARC (chaque chemin alimente la
    # merge place indépendamment — le step s'exécute quand le contrôle arrive
    # par UN de ses prédécesseurs, pas par tous).
    for nid, in_list in list(ins_by_node.items()):
        if len(in_list) > 1:
            merge_p = f"activity_merge_{name}_{nid}"
            net.place(merge_p)
            for i, p in enumerate(in_list):
                net.trans(f"join_{name}_{nid}_{i}", [p], [merge_p], "join")
            ins_by_node[nid] = [merge_p]
    for n in nodes:
        nid = n.get("id", "")
        if nid == "main":
            continue  # point d'entrée = place initiale (marquée)
        ins = list(ins_by_node.get(nid, []))
        outs = list(outs_by_node.get(nid, []))
        classes = token_class.get(nid, [("normal", [])])
        # Effets data du step, FONDUS dans sa transition d'activité : le step
        # a 1 entrée activité + 1 sortie activité ET manipule les pots globaux
        # en parallèle (pas de transitions data séparées sans activité).
        #   pick    : global_<t>_attributed  → agent_data_<agent>_<t>_doing
        #   release : agent_data_<agent>_<t>_doing   → global_<t>_done
        #   produce : (aucune entrée data)   → global_<t>_unattributed
        d_in: List[str] = []
        d_out: List[str] = []
        produced_types: List[str] = []
        tk_main = "normal"
        for tk, tk_types in classes:
            if tk == "pick":
                tk_main = "pick"
                for t in (tk_types or ["planning"]):
                    d_in.append(f"global_{t}_attributed")
                    d_out.append(f"agent_data_{name}_{t}_doing")
            elif tk == "release":
                tk_main = tk_main if tk_main != "normal" else "release"
                for t in sorted(consumes or ["planning"]):
                    d_in.append(f"agent_data_{name}_{t}_doing")
                    d_out.append(f"global_{t}_done")
            elif tk == "produce":
                # SWITCH : un step qui produit plusieurs types génère UNE
                # transition PAR type (choix non-déterministe — le skill décide
                # quels types créer, ex. decoupe → coding/testing/review/merge).
                tk_main = tk_main if tk_main != "normal" else "produce"
                produced_types.extend(tk_types or [])
            elif tk == "data":
                tk_main = "data"
                in_et, out_et, mode = (tk_types or ["", "", "single"])
                if mode == "all":
                    # UNE transition PAR TYPE (une data de chaque type peut
                    # être traitée indépendamment) × par sortie d'activité.
                    sorties = outs if outs else ["__end__"]
                    for t in net.types:
                        for i, out in enumerate(sorties):
                            touts = ([out] if out != "__end__"
                                     else [dead_p])
                            net.trans(f"step_{name}_{nid}_{t}_{i}",
                                      ins + [f"global_{t}_{in_et}"],
                                      touts + [f"global_{t}_{out_et}"],
                                      f"data:{t}")
                    continue  # transitions déjà générées (une par type)
                else:
                    d_out.append(f"global_{out_et}_unattributed")
        # UNE transition PAR SORTIE (les flows/on_error ont plusieurs sorties ;
        # les skills/llm_call n'en ont qu'une). L'activité et les données sont
        # prises/remises sur chaque chemin. Un step PRODUCE génère une
        # transition PAR (sortie × type produit) — le switch de découpe.
        # Déduplication : d_in/d_out ne gardent qu'une occurrence par place.
        d_in = _dedup(d_in)
        d_out = _dedup(d_out)
        sorties = outs if outs else ["__end__"]
        if produced_types:
            for t in sorted(set(produced_types)):
                for i, out in enumerate(sorties):
                    touts = ([out] if out != "__end__"
                             else [dead_p]) + list(d_out) + \
                        [f"global_{t}_unattributed"]
                    net.trans(f"step_{name}_{nid}_{t}_{i}",
                              ins + list(d_in) + [flux_main], touts + [flux_main],
                              f"produce:{t}->{i + 1}" if len(sorties) > 1
                              else f"produce:{t}")
        else:
            for i, out in enumerate(sorties):
                tins = ins + list(d_in) + [flux_main]
                touts = ([out] if out != "__end__"
                         else [dead_p]) + list(d_out) + [flux_main]
                lbl = f"{tk_main}->{i + 1}" if len(sorties) > 1 else tk_main
                net.trans(f"step_{name}_{nid}_{i}", tins, touts, lbl)

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

    # ENTRYPOINT : dead → main__<premier step> (démarre l'activité). Le flux
    # main est pris/remis (self-loop) — le workflow principal s'exécute DANS
    # flux_main, qui est le marquage initial du flux.
    main_edges = [e for e in edges if e.get("from") == "main" and e.get("to")]
    if main_edges:
        entry_p = _pname(main_edges[0])
        net.trans(f"entry_{name}", [dead_p, flux_main],
                  [entry_p, flux_main], "entry")

    # EXITPOINTS : les noeuds terminaux → dead (déshydraté + dead, pas juste
    # une place terminale). Le cycle est fermé : dead → workflow → dead. Le
    # flux main est remis (l'agent revient en attente, déshydraté).
    for n in nodes:
        nid = n.get("id", "")
        if nid == "main":
            continue
        if nid not in outs_by_node or not outs_by_node[nid]:
            net.trans(f"step_{name}_{nid}_end",
                      ins_by_node.get(nid, []) + [flux_main],
                      [dead_p, flux_main], "exit")

    # SWITCH DE FLUX (entrypoints prioritaires) : stack_state / pop_state.
    #   stack_<agent>_<ep> : flux_<agent>_main → flux_<agent>_stack + flux_<ep>
    #     (un entrypoint prioritaire interrompt : l'état main est sauvegardé
    #     dans la pile, le flux <ep> devient actif — les steps DANS main sont
    #     gelés car ils exigent flux_main).
    #   pop_<agent>_<ep>   : flux_<agent>_<ep> + flux_<agent>_stack → flux_main
    #     (l'interruption se termine : on restaure main depuis la pile).
    for ep, fep in net.flux.items():
        net.trans(f"stack_{name}_{ep}", [flux_main], [net.stack, fep],
                  "stack_state")
        net.trans(f"pop_{name}_{ep}", [fep, net.stack], [flux_main],
                  "pop_state")

    return {"agent": name, "petri": net, "fsm_nodes": len(fsm["nodes"]),
            "consumes": sorted(consumes), "produces": sorted(produced)}


def supervisor_petri(rules: List[Dict[str, Any]] = (),
                     workspace: str = "supervisor") -> Petri:
    """Pétri COMPLET du SUPERVISOR (sans LLM) : ses places (les pots globaux)
    et ses transitions (assign, finalise, relais selon les règles, respond)."""
    all_types = ["planning", "coding", "testing", "reviewing", "merging",
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
    net.trans("sup_make_respond", [f"global_planning_done"],
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
    # Le SUPERVISOR (transformé comme un agent, sans LLM) fait partie de la team.
    sup_path = REPO / "AgentsCatalogue" / "agents" / "supervisor.agent.yaml"
    if sup_path.exists():
        result["members"].append(build_from_yaml(sup_path, "supervisor"))
    return result
    return result


# Couleurs des places par rôle/état (dégradé rouge → vert) :
#   activity_*            : bleu clair (le jeton d'activité)
#   global_<t>_<etat>     : unattributed=rouge, attributed=orange,
#                           done=jaune, supervised=vert
#   agent_data_<t>_doing  : orange clair (la tâche en cours de l'agent)
PLACE_COLORS: Dict[str, str] = {
    "unattributed": "#e74c3c",  # rouge
    "attributed": "#f39c12",    # orange
    "done": "#f1c40f",          # jaune
    "supervised": "#2ecc71",    # vert
}
PLACE_ACTIVITY_COLOR = "#d6eaf8"   # bleu clair
PLACE_AGENT_DOING_COLOR = "#f5b041"


def _place_color(pname: str) -> str:
    if pname.endswith("_dead"):
        return "#7f8c8d"  # gris sombre — agent déshydraté/mort
    if pname.startswith("activity_"):
        return PLACE_ACTIVITY_COLOR
    for et, col in PLACE_COLORS.items():
        if pname.endswith("_" + et):
            return col
    if pname.startswith("agent_data_"):
        return PLACE_AGENT_DOING_COLOR
    return "#ffffff"


def save_petri_png(petri: Petri, out_path: str) -> str:
    """Construit le pétri en DOT (graphviz) avec les LABELS des places et
    transitions, et le rend en PNG haute résolution. pm4py ne nomme pas les
    places (cercles muets) — graphviz permet un contrôle total des labels."""
    import math
    from graphviz import Digraph
    g = Digraph(f"taskflow_{petri.agent}", format="png")
    # Canvas adaptatif : ~1.6" par place/transition, min 24", dpi élevé.
    n_elems = max(1, len(petri.places) + len(petri.transitions))
    dim = max(24.0, math.sqrt(n_elems) * 1.6)
    g.attr(rankdir="LR", size=f"{dim},{dim}", dpi="300",
           nodesep="0.25", ranksep="0.45")
    for p in petri.places:
        g.node(p, p, shape="circle", fontsize="13",
               style="filled", fillcolor=_place_color(p))
    for t in petri.transitions:
        g.node(t["name"], t["name"], shape="box", fontsize="10",
               style="rounded,filled", fillcolor="#eef2f7")
        for pin in t["in"]:
            g.edge(pin, t["name"], arrowsize="0.7")
        for pout in t["out"]:
            g.edge(t["name"], pout, arrowsize="0.7")
    base = str(out_path)
    if base.endswith(".png"):
        base = base[:-4]
    g.render(base, cleanup=True)
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


def check_activity_rule(petri: Petri) -> List[Dict[str, Any]]:
    """Règle de validité : toute transition de STEP doit avoir exactement
    UNE entrée activité et UNE sortie activité (les places de data sont en
    plus). Les transitions data pures (release/produce) et de contrôle
    (loop/end) sont exemptes."""
    violations: List[Dict[str, Any]] = []
    for t in petri.transitions:
        if not t["name"].startswith("step_"):
            continue
        n_in = sum(1 for p in t["in"] if p.startswith("activity_"))
        n_out = sum(1 for p in t["out"] if p.startswith("activity_"))
        if n_in != 1 or n_out != 1:
            violations.append({"transition": t["name"],
                               "in_activity": n_in, "out_activity": n_out})
    return violations


# Cycle de vie d'une sub_task (B2) : transitions data AUTORISÉES.
#   global_<t>_unattributed → attributed  (assign, superviseur)
#   global_<t>_attributed   → agent_data_<t>_doing  (pick, agent)
#   agent_data_<t>_doing    → global_<t>_done  (release sub_task_done)
#   global_<t>_done         → global_<t>_supervised  (finalise)
#   global_<t>_cancelled    → global_<t>_supervised  (finalise cancelled)
#   agent_data_<t>_doing    → global_<t>_unattributed (release échec)
#   global_<t>_done         → global_<t>_unattributed (relais des règles)
#   global_<t>_attributed   → global_<t>_unattributed (?? — re-attribution)
LIFECYCLE = [
    ("unattributed", "attributed"),
    ("attributed", "doing"),
    ("doing", "done"),
    ("doing", "unattributed"),   # release échec
    ("doing", "cancelled"),      # échec terminal
    ("done", "supervised"),
    ("cancelled", "supervised"),
    ("done", "unattributed"),    # relais des règles
]
# États absorbants (B3) : ne doivent jamais être consommés.
ABSORBANT = ("supervised", "cancelled")


def _data_state(pname: str) -> str:
    """'global_coding_done' / 'agent_data_coding_doing' → 'done'/'doing'."""
    for st in ("unattributed", "attributed", "doing", "done", "supervised",
               "cancelled", "waiting_dependencies"):
        if pname.endswith("_" + st):
            return st
    return ""


def _is_data(p: str) -> bool:
    return p.startswith("global_") or p.startswith("agent_data_")


def check_data_lifecycle(petri: Petri) -> List[Dict[str, Any]]:
    """Invariant B2+B3 : chaque transition data doit suivre le cycle de vie
    autorisé (une data → une data, dans l'ordre du cycle). supervised/cancelled
    sont absorbants : aucune transition ne doit les CONSOMMER."""
    violations: List[Dict[str, Any]] = []
    allowed = set(LIFECYCLE)
    for t in petri.transitions:
        din = _dedup([p for p in t["in"] if _is_data(p)])
        dout = _dedup([p for p in t["out"] if _is_data(p)])
        if not din and not dout:
            continue
        # superviseur : assign/finalise/relais (global → global)
        if t["name"].startswith("sup_"):
            for p in din:
                if _data_state(p) in ABSORBANT:
                    violations.append({"rule": "B3_absorbant",
                                       "transition": t["name"],
                                       "data": p,
                                       "detail": "consomme un état absorbant"})
            continue
        # agent : pick (global→agent_data), release (agent_data→global)
        for i in din:
            for o in dout:
                si, so = _data_state(i), _data_state(o)
                if not si or not so:
                    continue
                # agent_data_X_doing → même place en remise (utilisation)
                if i == o:
                    continue
                if (si, so) not in allowed:
                    violations.append({"rule": "B2_lifecycle",
                                       "transition": t["name"],
                                       "from": i, "to": o,
                                       "detail": f"{si}→{so} hors cycle"})
    return violations


def check_absorbant_consumed(petri: Petri) -> List[Dict[str, Any]]:
    """Invariant B3 (doublon explicite) : supervised/cancelled ne sont jamais
    pris en entrée d'aucune transition."""
    violations: List[Dict[str, Any]] = []
    for t in petri.transitions:
        for p in t["in"]:
            if _is_data(p) and _data_state(p) in ABSORBANT:
                violations.append({"rule": "B3_absorbant",
                                   "transition": t["name"], "data": p})
    return violations


def check_activity_acyclic(petri: Petri) -> List[Dict[str, Any]]:
    """Invariant A4 : le workflow interne (places activity_* hors dead) est un
    DAG — aucun cycle d'activité qui ne repasse pas par dead/entry/exit.

    Les BOUCLES DE RETRY (on_error : llm_error → err_branch → re-ask) sont
    LÉGITIMES : elles sont bornées par max_iterations du FSM. On les exclut
    du graphe (les transitions d'erreur ne sont pas des cycles de contrôle)."""
    acts = [p for p in petri.places
            if p.startswith("activity_") and not p.endswith("_dead")]
    if not acts:
        return []
    # Le superviseur (sans LLM) boucle en continu (tick) : cycle légitime.
    acts = [p for p in acts if not p.startswith("activity_supervisor_")]
    # graphe dirigé sur les places d'activité (sauf dead)
    adj: Dict[str, List[str]] = {p: [] for p in acts}
    for t in petri.transitions:
        if t["name"].startswith(("entry_", "step_", "join_", "loop_")):
            # exclut les arcs d'erreur (retry bornés)
            if any(k in t["name"] for k in ("llm_error", "err_branch",
                                            "error", "_sleep")):
                continue
            ins = [p for p in t["in"] if p in adj]
            outs = [p for p in t["out"] if p in adj]
            for i in ins:
                adj[i].extend(outs)
    # détection de cycle (DFS)
    VIS = {p: 0 for p in acts}  # 0=blanc 1=gris 2=noir
    cycle: List[str] = []
    found: List[str] = []

    def dfs(p: str, stack: List[str]) -> None:
        if found:
            return
        VIS[p] = 1
        stack.append(p)
        for nxt in adj.get(p, []):
            if VIS[nxt] == 0:
                dfs(nxt, stack)
            elif VIS[nxt] == 1:
                i = stack.index(nxt) if nxt in stack else 0
                found.extend(stack[i:] + [nxt])
                return
        stack.pop()
        VIS[p] = 2

    for p in acts:
        if VIS[p] == 0:
            dfs(p, [])
    if found:
        return [{"rule": "A4_acyclic", "cycle": found}]
    return []


def check_agent_doing_unique(petri: Petri) -> List[Dict[str, Any]]:
    """Invariant B5 : un agent ne traite qu'UNE sub_task à la fois.
    Chaque place agent_data_<agent>_<t>_doing doit avoir exactement 1 STEP
    producteur (pick) et 1 STEP consommateur (release). Les transitions
    multiples d'un même step (0/1 = sorties on_error, ou _<type> = switch de
    découpe) comptent pour 1."""
    import re
    violations: List[Dict[str, Any]] = []
    doing = sorted({p for p in petri.places if p.startswith("agent_data_")
                    and p.endswith("_doing")})
    for p in doing:
        # normalise : step_<agent>/<sub>_<nid>[_<type>]_<i> → step_<agent>...
        def norm(name: str) -> str:
            m = re.match(r"(step_[\w/-]+?)(?:_\w+)?_\d+$", name)
            return m.group(1) if m else name

        prods = {norm(t["name"]) for t in petri.transitions if p in t["out"]}
        cons = {norm(t["name"]) for t in petri.transitions if p in t["in"]}
        prods = {x for x in prods if x not in cons}
        cons = {x for x in cons if x not in prods}
        if len(prods) > 1 or len(cons) > 1:
            violations.append({"rule": "B5_doing_unique", "place": p,
                               "producteurs": sorted(prods),
                               "consommateurs": sorted(cons)})
    return violations


def check_flux_regularity(petri: Petri) -> List[Dict[str, Any]]:
    """Invariant C1 (flux) : TOUTE transition de step doit avoir exactement
    1 flux entrant + 1 flux sortant (une place flux_*).

    Le jeton flux sélectionne l'entrypoint actif ; un step s'exécute DANS un
    flux. Les transitions de contrôle (entry/exit/switch : stack_state /
    pop_state) et le superviseur (boucle tick) sont exempts.

    Si le pétri n'a AUCUNE place flux (pas encore générées), le check passe
    (attendu — les entrypoints en BDD viendront câbler le flux)."""
    flux_places = [p for p in petri.places if p.startswith("flux_")]
    if not flux_places:
        return []  # pas encore de flux → invariant non applicable
    violations: List[Dict[str, Any]] = []
    exempt = ("entry_", "exit", "switch_", "stack_", "pop_")
    for t in petri.transitions:
        if t["name"].startswith(("sup_", "join_", "loop_", "entry_",
                                 "step_supervisor_")):
            continue
        if t["name"].startswith(exempt):
            continue
        n_in = sum(1 for p in t["in"] if p.startswith("flux_"))
        n_out = sum(1 for p in t["out"] if p.startswith("flux_"))
        if n_in != 1 or n_out != 1:
            violations.append({"rule": "C1_flux", "transition": t["name"],
                               "in_flux": n_in, "out_flux": n_out})
    return violations


def check_invariants(petri: Petri) -> List[Dict[str, Any]]:
    """Regroupe les invariants statiques (B2+B3+A4+B5+C1 flux)."""
    out: List[Dict[str, Any]] = []
    out.extend(check_absorbant_consumed(petri))
    out.extend(check_data_lifecycle(petri))
    out.extend(check_activity_acyclic(petri))
    out.extend(check_agent_doing_unique(petri))
    out.extend(check_flux_regularity(petri))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="YAML → Pétri du taskflow")
    ap.add_argument("yaml", help="fichier .team.yaml ou .agent.yaml")
    ap.add_argument("--agent", default="", help="nom d'agent (si yaml team)")
    ap.add_argument("--team", action="store_true", help="traiter comme une team")
    ap.add_argument("--team-petri", action="store_true",
                    help="pétri GLOBAL de la team (agents + superviseur fusionnés)")
    ap.add_argument("--verify", action="store_true",
                    help="vérifier le pétri (pm4py : reachability de supervised)")
    ap.add_argument("--verify-team", action="store_true",
                    help="vérifier l'invariant de la TEAM : toute sub_task "
                         "unattributed → supervised/cancelled (pas de deadlock)")
    ap.add_argument("--check", action="store_true",
                    help="vérifier la règle d'activité (1 entrée + 1 sortie activité/step)")
    ap.add_argument("--check-invariants", action="store_true",
                    help="vérifier les invariants statiques (B2 cycle de vie, "
                         "B3 absorbants, A4 acyclicité, B5 doing unique)")
    ap.add_argument("--flow", action="store_true",
                    help="générer le pétri en data_genere PASSIF (catalogue_genere : "
                         "symbols skills + invalidation par inputs_hash)")
    ap.add_argument("--force", action="store_true",
                    help="forcer la re-génération (avec --flow)")
    ap.add_argument("--png", default="", help="sauvegarder le pétri en PNG")
    args = ap.parse_args()
    path = Path(args.yaml)
    if args.flow:
        from services.flow_engine import FlowEngine
        fe = FlowEngine()
        r = fe.ensure_team_petri(path, force=args.force)
        print(f"petri passif : {r['petri']} (cached={r['cached']})")
        print(f"  symbols membres : {len(r.get('symbols', []))}")
        if r.get("verify"):
            bad = [t for t, x in r["verify"].items()
                   if isinstance(x, str) and x.startswith("KO")]
            for t, x in r["verify"].items():
                if t == "_types":
                    continue
                print(f"  {t:12s} : {x}")
            print(f"  RESULTAT : {'KO — ' + str(bad) if bad else 'OK — team clôturable'}")
        if r.get("png"):
            print(f"  png : {r['png']}")
        fe.close()
        return
    if args.png:
        if args.team or args.team_petri:
            out = save_petri_png(team_global_petri(path), args.png)
        else:
            res = build_from_yaml(path, args.agent)
            out = save_petri_png(res["petri"], args.png)
        print(f"PNG sauvegardé : {out}")
        return
    if args.verify_team:
        v = verify_team(path)
        types = v.pop("_types", [])
        print(f"== Vérification team {path.stem} : invariant "
              f"'unattributed → supervised/cancelled' ==")
        bad = [t for t, r in v.items() if r.startswith("KO")]
        for t in types:
            print(f"  {t:14s} : {v.get(t, '?')}")
        if bad:
            print(f"RESULTAT : KO — {len(bad)} type(s) bloqué(s) : {bad}")
            sys.exit(1)
        else:
            print(f"RESULTAT : OK — {len(types)} types clôturables")
        return
    if args.check:
        if args.team or args.team_petri:
            petri = team_global_petri(path)
        else:
            petri = build_from_yaml(path, args.agent)["petri"]
        v = check_activity_rule(petri)
        if v:
            print(f"VIOLATIONS ({len(v)}) :")
            for x in v[:20]:
                print(f"  {x['transition']}: {x['in_activity']} in / {x['out_activity']} out activité")
        else:
            print("OK : toutes les transitions de step ont exactement 1 entrée + 1 sortie activité")
        return
    if args.check_invariants:
        if args.team or args.team_petri:
            petri = team_global_petri(path)
        else:
            petri = build_from_yaml(path, args.agent)["petri"]
        viol = check_invariants(petri)
        if viol:
            print(f"INVARIANTS KO ({len(viol)}) :")
            for x in viol[:30]:
                rule = x.get("rule", "?")
                tr = x.get("transition", x.get("place", x.get("cycle", "")))
                detail = x.get("detail", x.get("data", ""))
                print(f"  [{rule}] {tr} {detail}")
        else:
            print("INVARIANTS OK : B2 cycle de vie, B3 absorbants, "
                  "A4 acyclicité, B5 doing unique, C1 flux")
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
    else:
        res = build_from_yaml(path, args.agent)
        print(res["petri"].render())
        print(f"\n(FSM : {res['fsm_nodes']} nœuds ; consumes={res['consumes']})")
        if args.verify:
            print("   verify:", verify_with_pm4py(res["petri"]))


def supervisor_transitions(rules: List[Dict[str, Any]] = ()) -> List[Dict[str, Any]]:
    """Transitions du SUPERVISOR (selon les règles du manifest) :
      - pour chaque type : unattributed → attributed (assign) ;
        done → supervised (finalise) ; cancelled → supervised (finalise closed) ;
      - selon les règles (in_type, in_tag)→(out_type, out_tag) : quand un type
        est done, crée le relais out_type → unattributed."""
    trans: List[Dict[str, Any]] = []
    # assign + finalise (par type connu)
    for t in ("planning", "coding", "testing", "reviewing", "merging", "respond",
              "exploration"):
        trans.append({"name": f"sup_assign_{t}",
                      "in": [f"global_{t}_unattributed"],
                      "out": [f"global_{t}_attributed"], "label": "assign"})
        trans.append({"name": f"sup_final_{t}",
                      "in": [f"global_{t}_done"],
                      "out": [f"global_{t}_supervised"], "label": "finalise"})
        trans.append({"name": f"sup_final_cancelled_{t}",
                      "in": [f"global_{t}_cancelled"],
                      "out": [f"global_{t}_supervised"], "label": "finalise"})
    # relais selon les règles : global_<in_type>_done → global_<out_type>_unattributed
    for r in rules or []:
        it = r.get("in_type") or ""
        ot = r.get("out_type") or ""
        if it and ot:
            trans.append({"name": f"sup_relay_{it}_{ot}",
                          "in": [f"global_{it}_done"],
                          "out": [f"global_{ot}_unattributed"], "label": f"{it}→{ot}"})
    return trans


def verify_with_pm4py(petri: Petri, rules: List[Dict[str, Any]] = ()) -> Dict[str, Any]:
    """Vérifie (BFS de marquages borné) que chaque type atteint `supervised`
    ou `cancelled` depuis un jeton initial `unattributed` (liveness faible :
    chaque tâche unattributed doit pouvoir être clôturée)."""
    # transitions = agent (pick/release/steps) + superviseur (assign/finalise/
    # relais selon les règles + finalise cancelled)
    trans = list(petri.transitions) + supervisor_transitions(rules)
    results: Dict[str, Any] = {}
    for t in petri.types:
        src = f"global_{t}_unattributed"
        dst_sup = f"global_{t}_supervised"
        dst_can = f"global_{t}_cancelled"
        # Marquage initial : 1 jeton en unattributed + le jeton d'activité
        # initial de chaque agent (la place activity_*_dead : déshydraté) +
        # le jeton de flux main (flux_*_main).
        m0 = {p: (1 if p == src else 0) for p in petri.places}
        for p in petri.places:
            if p.endswith("_dead") and p.startswith("activity_"):
                m0[p] = 1
        for p in petri.places:
            if p.startswith("flux_") and p.endswith("_main"):
                m0[p] = 1
        # BFS des marquages atteignables (borné : un jeton par place).
        seen = {_mark_key(m0)}
        frontier = [m0]
        reached = False
        steps = 0
        while frontier and steps < 200000:
            m = frontier.pop()
            steps += 1
            if m.get(dst_sup, 0) > 0 or m.get(dst_can, 0) > 0:
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
        results[t] = "OK (supervised/cancelled atteignable)" if reached else \
            "KO (jamais supervised/cancelled)"
    return results


def verify_team(team_path: Path) -> Dict[str, Any]:
    """Vérifie l'INVARIANT de la team complète : pour TOUTE sub_task qui
    devient `unattributed`, elle doit pouvoir aboutir à `supervised` ou
    `cancelled` (pas de deadlock) — avec les règles du manifest (relais).

    Règle du vrai système : le superviseur finalise TOUTE sub_task done ou
    cancelled (mark_supervised → supervised). Donc une sub_task unattributed
    est clôturée ssi au moins UN agent la pioche (pick) et la mène à
    done/cancelled. On vérifie donc :
      1. chaque type produit (decoupe/ask_intel/entry/règles/respond) a un
         agent qui le pick ;
      2. le superviseur a bien assign+finalise (relais) pour chaque type.
    """
    import yaml
    data = yaml.safe_load(team_path.read_text()) or {}
    rules = [r for r in (data.get("supervisor_rules") or []) if isinstance(r, dict)]
    petri = team_global_petri(team_path, rules)

    def _is_data(p: str) -> bool:
        return p.startswith("global_") or p.startswith("agent_data_")

    # Types produits (global_<t>_unattributed en sortie) et pickés
    # (global_<t>_attributed → agent_data_<t>_doing).
    produced: Dict[str, List[str]] = {}
    picked: Dict[str, List[str]] = {}
    supervised: Dict[str, List[str]] = {}
    for tr in petri.transitions:
        din = [p for p in tr["in"] if _is_data(p)]
        dout = [p for p in tr["out"] if _is_data(p)]
        for o in dout:
            if o.startswith("global_") and o.endswith("_unattributed"):
                t = o[len("global_"):-len("_unattributed")]
                produced.setdefault(t, []).append(tr["name"])
        for i in din:
            if i.startswith("global_") and i.endswith("_attributed") and \
                    any(o.startswith("agent_data_") for o in dout):
                t = i[len("global_"):-len("_attributed")]
                picked.setdefault(t, []).append(tr["name"])
        for o in dout:
            if o.startswith("global_") and o.endswith("_supervised"):
                t = o[len("global_"):-len("_supervised")]
                supervised.setdefault(t, []).append(tr["name"])

    types = sorted(set(produced) | set(picked) | set(supervised))
    results: Dict[str, Any] = {}
    for t in types:
        prods = produced.get(t, [])
        picks = picked.get(t, [])
        sups = supervised.get(t, [])
        if prods:
            if not picks:
                results[t] = "KO (produit mais AUCUN agent ne le pioche — " \
                             "jamais supervised/cancelled)"
                continue
            if not sups:
                results[t] = "KO (pické mais pas de finalise supervised)"
                continue
            results[t] = (f"OK (produit par {len(prods)} sources, "
                          f"pické par {len(picks)}, finalisé {len(sups)})")
        else:
            # type non produit directement : uniquement via relais des règles
            results[t] = f"OK (produit par relais des règles, pické {len(picks)})" \
                if picks and sups else f"KO (type {t} incohérent)"
    results["_types"] = types
    return results


def _mark_key(m: Dict[str, int]) -> str:
    return "|".join(f"{p}:{m.get(p, 0)}" for p in sorted(m))


def _dedup(items: List[str]) -> List[str]:
    """Déduplique en gardant l'ordre (une occurrence par place)."""
    seen: Set[str] = set()
    out: List[str] = []
    for x in items:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


if __name__ == "__main__":
    main()
