"""flow_engine — génère et invalide les data_genere (symbols, pétris).

Branche le traducteur (`flow_translator`) sur le catalogue genere
(`catalogue_genere`) :
  - `ensure_skill_symbols` : analyse chaque skill des agents/team, enregistre
    un `symbol` (gen_data, kind=symbol) + ses dépendances (gen_dependance) ;
  - `ensure_supervisor` : résout le superviseur depuis les règles du manifest ;
  - invalidation par `inputs_hash` : si l'empreinte (version du traducteur +
    hash des fichiers sources) diffère du stocké, on re-génère.

Usage:
    from services.flow_engine import FlowEngine
    fe = FlowEngine()
    fe.ensure_team_symbols("services/manifests/teams/llm-code.team.yaml")
    fe.close()
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.sql.catalogue_genere import GenereCatalogue
from services.flow_translator import python_translator, skill_symbol, \
    supervisor_symbol

REPO = Path(__file__).resolve().parent.parent

# Version du traducteur : incluse dans l'inputs_hash → changer le traducteur
# invalide tous les symbols générés.
TRANSLATOR_VERSION = "0.1"


def file_hash(path: Path) -> str:
    try:
        return hashlib.sha1(path.read_bytes()).hexdigest()
    except OSError:
        return ""


class FlowEngine:
    """Moteur de génération des data_genere (skills + supervisor + pétri).

    Le point d'entrée unifié est `get_data_genere` : il résout la validité
    d'une data (status + inputs_hash), régénère si nécessaire, et appelle
    RÉCURSIVEMENT les générateurs des dépendances data_genere. Les
    `ensure_*` (ensure_skill_symbol, ensure_supervisor, ensure_team_petri)
    sont des générateurs enregistrés sous un `id_data` — ils consomment
    `get_data_genere` pour leurs propres dépendances.
    """

    def __init__(self, db_path: Optional[Path] = None, auto_flush: bool = True):
        self.gen = GenereCatalogue(db_path=db_path, auto_flush=auto_flush)
        self._gen_stack: List[str] = []  # prévention de cycles

    def close(self) -> None:
        try:
            self.gen.flush()
        except Exception:
            pass

    # ── get_data_genere : wrapper générique (validité + récursion) ──

    def get_data_genere(self, id_data: str, *, generator: str,
                        deps: Optional[List[Dict[str, Any]]] = None,
                        params: str = "", project_id: int = 0,
                        force: bool = False) -> Optional[Dict[str, Any]]:
        """Point d'entrée UNIFIÉ pour toute data_genere.

        `generator` : nom de générateur (ex. "petri", "symbol") enregistré
        dans `_GENERATORS`. Le GÉNÉRATEUR est la source de vérité de
        l'invalidation (il connaît ses fichiers/deps et calcule son propre
        inputs_hash). `get_data_genere` :
          1. résout RÉCURSIVEMENT les deps data_genere (spécifiées par
             {id, generator, params}) — une dep stale est re-générée ;
          2. vérifie la data courante : si elle existe et est VALID et son
             inputs_hash == celui du générateur → cache ;
          3. sinon → appelle le générateur (qui calcule son hash, upsert,
             enregistre ses deps).
        `force` : re-génère toujours (propagé aux deps)."""
        if id_data in self._gen_stack:
            raise RuntimeError(f"cycle de génération détecté : {id_data}")
        self._gen_stack.append(id_data)
        try:
            gen = _GENERATORS.get(generator)
            if gen is None:
                raise KeyError(f"générateur inconnu : {generator}")
            # 1) résout récursivement les deps data_genere (stale → re-gen)
            for dep_spec in deps or []:
                dep_id = dep_spec.get("id", "")
                dep_gen = dep_spec.get("generator", "")
                dep_params = dep_spec.get("params", "")
                if not dep_id or not dep_gen:
                    continue
                dep = self.gen.get(project_id, dep_id)
                # dep manquante ou stale → re-résolution récursive
                if dep is None or dep.get("status") != "valid":
                    self.get_data_genere(dep_id, generator=dep_gen,
                                         params=dep_params,
                                         project_id=project_id, force=force)
            # 2) la data courante est-elle valide ? (le générateur vérifie
            #    son propre inputs_hash via la méthode ensure_* associée)
            existing = self.gen.get(project_id, id_data)
            if existing and existing.get("status") == "valid" and not force:
                # l'invalidation fine est laissée au générateur : on relance
                # le générateur qui décidera cache vs re-gen via son hash.
                pass
            # 3) appelle le générateur (il gère hash/cache/upsert/deps)
            return gen(self, id_data=id_data, project_id=project_id,
                       params=params, force=force, deps=deps or [])
        finally:
            self._gen_stack.pop()

    # ── Skills ──────────────────────────────────────────────

    def _inputs_hash(self, symbol_ref: str, files: List[Path],
                     params: str = "") -> str:
        h = hashlib.sha1()
        h.update(TRANSLATOR_VERSION.encode())
        h.update(symbol_ref.encode())
        h.update(params.encode())
        for f in files:
            h.update(file_hash(f).encode())
        return h.hexdigest()

    def ensure_skill_symbol(self, skill_ref: str,
                            force: bool = False) -> Optional[Dict[str, Any]]:
        """Génère le symbol d'un skill si nécessaire (invalidation par hash)."""
        import yaml
        # Résout le .yaml du skill (workspace/decoupe@v1 → .../workspace/decoupe@v1.skill.yaml)
        base = skill_ref.split("@")[0]
        path = REPO / "AgentsCatalogue" / "skills" / f"{base}@v1.skill.yaml"
        if not path.exists():
            hits = list((REPO / "AgentsCatalogue" / "skills").glob(f"{base}*.yaml"))
            if not hits:
                return None
            path = hits[0]
        try:
            data = yaml.safe_load(path.read_text()) or {}
        except Exception:
            return None
        impl = data.get("implementation") or {}
        fn = impl.get("function") if isinstance(impl, dict) else None
        if not fn:
            return None
        sym = python_translator.translate(fn)
        if sym is None:
            return None
        project_id = 0
        id_data = f"symbol:{sym.path}"
        # fichiers sources de l'implémentation
        src_files: List[Path] = []
        src = REPO / sym.path.split(":")[0]
        if src.exists():
            src_files.append(src)
        # hash des dépendances (imports locaux résolus)
        dep_files = []
        for d in sym.dependencies:
            if d.get("file"):
                f = REPO / d["file"]
                if f.exists():
                    dep_files.append(f)
        ihash = self._inputs_hash(sym.ref, [src] + dep_files)
        existing = self.gen.get(project_id, id_data)
        if existing and existing.get("inputs_hash") == ihash and not force:
            return existing
        self.gen.upsert(
            project_id, id_data, name=sym.ref, kind=sym.kind, path=sym.path,
            ref_id=skill_ref, value=json.dumps(sym.to_dict()),
            dependencies_json=[{"dep_ref": d.get("dep_ref", ""),
                                "version": "", "role": d.get("role", "import")}
                               for d in sym.dependencies],
            inputs_hash=ihash, status="valid",
            generation_mode=sym.generation_mode)
        for d in sym.dependencies:
            self.gen.add_dependency(project_id, id_data,
                                    d.get("dep_ref", ""), "",
                                    d.get("role", "import"))
        return self.gen.get(project_id, id_data)

    def ensure_team_symbols(self, team_path: Path,
                            force: bool = False) -> Dict[str, Any]:
        """Analyse les agents d'une team + leurs skills + le superviseur."""
        import yaml
        data = yaml.safe_load(team_path.read_text()) or {}
        members = data.get("members") or []
        rules = [r for r in (data.get("supervisor_rules") or []) if isinstance(r, dict)]
        skills: List[str] = []
        for m in members:
            ref = m.get("ref", "")
            apath = REPO / "AgentsCatalogue" / "agents" / f"{ref}.agent.yaml"
            if not apath.exists():
                continue
            try:
                acfg = yaml.safe_load(apath.read_text()) or {}
            except Exception:
                continue
            skills.extend(self._collect_skills(acfg))
            for sa in (m.get("sub_agents") or []):
                saref = sa.get("ref", "")
                sapath = REPO / "AgentsCatalogue" / "agents" / f"{saref}.agent.yaml"
                if sapath.exists():
                    try:
                        skills.extend(self._collect_skills(
                            yaml.safe_load(sapath.read_text()) or {}))
                    except Exception:
                        pass
        skills = sorted(set(skills))
        generated, missing = [], []
        for s in skills:
            r = self.ensure_skill_symbol(s, force=force)
            (generated if r else missing).append(s)
        sup = self.ensure_supervisor(rules, data.get("name", team_path.stem),
                                     force=force)
        return {"skills": generated, "missing": missing,
                "supervisor": sup, "rules": rules}

    def _collect_skills(self, agent_cfg: Dict[str, Any]) -> List[str]:
        skills: List[str] = []
        steps = (agent_cfg.get("entrypoints") or {}).get("main", {}).get("steps", [])

        def walk(steps: List[Dict[str, Any]]) -> None:
            for s in steps or []:
                if s.get("type") == "llm_call":
                    for sk in (s.get("skills") or []):
                        if sk and sk not in skills:
                            skills.append(sk)
                if s.get("type") in ("while", "for", "if", "group"):
                    body = s.get("body", {})
                    sub = body.get("steps", body) if isinstance(body, dict) else body
                    if isinstance(sub, list):
                        walk(sub)

        walk(steps)
        return skills

    # ── Supervisor ──────────────────────────────────────────

    def ensure_supervisor(self, rules: List[Dict[str, Any]],
                          team_name: str, force: bool = False) -> Dict[str, Any]:
        """Résout le superviseur selon les règles (create out_type + respond)."""
        sym = supervisor_symbol(rules, team_name)
        project_id = 0
        id_data = f"symbol:{sym.path}"
        # paramètres = règles (détermine le contract)
        params = json.dumps(rules, sort_keys=True)
        files = [REPO / "services" / "task_supervisor" / "service.py",
                 REPO / "modules" / "sql" / "workspace.py",
                 REPO / "modules" / "sql" / "workspace_schema.sql"]
        ihash = self._inputs_hash(sym.ref, files, params)
        existing = self.gen.get(project_id, id_data)
        if existing and existing.get("inputs_hash") == ihash and not force:
            return existing
        self.gen.upsert(
            project_id, id_data, name=sym.ref, kind=sym.kind, path=sym.path,
            ref_id=f"team:{team_name}", value=json.dumps(sym.to_dict()),
            dependencies_json=[{"dep_ref": d["dep_ref"],
                                "version": "", "role": d["role"]}
                               for d in sym.dependencies],
            inputs_hash=ihash, status="valid",
            generation_mode="deterministic")
        for d in sym.dependencies:
            self.gen.add_dependency(project_id, id_data, d["dep_ref"], "",
                                    d["role"])
        return self.gen.get(project_id, id_data)

    # ── Diff / backup ───────────────────────────────────────

    def backup(self, project_id: int, id_data: str, reason: str = "") -> str:
        bid = self.gen.log_backup(project_id, id_data, reason=reason)
        return bid or ""

    # ── Pétri (data_genere passif) ──────────────────────────

    def ensure_team_petri(self, team_path: Path,
                          force: bool = False) -> Dict[str, Any]:
        """Construit le pétri GLOBAL d'une team (data_genere passif).

        PONT vers `get_team_petri` (le chemin unifié via get_data_genere) :
        garde la signature historique (utilisée par la CLI --flow et les
        callers) mais délègue la génération + invalidation au wrapper."""
        import yaml
        data = yaml.safe_load(team_path.read_text()) or {}
        team_name = data.get("name", team_path.stem)
        id_data = f"petri:{team_name}"
        result = self.get_team_petri(team_path, force=force)
        cached = self.gen.get(0, id_data)
        return {
            "petri": id_data,
            "cached": bool(cached and cached.get("status") == "valid"),
            "hash": (cached or {}).get("inputs_hash", ""),
            "symbols": self._team_skill_symbol_ids(team_path),
            "verify": (result or {}).get("verify"),
            "png": (result or {}).get("png"),
        }

    def _team_member_paths(self, team_path: Path) -> List[str]:
        """chemins des agents d'une team (membres + sous-agents)."""
        import yaml
        data = yaml.safe_load(team_path.read_text()) or {}
        out: List[str] = []
        for m in data.get("members", []) or []:
            ref = m.get("ref", "")
            apath = REPO / "AgentsCatalogue" / "agents" / f"{ref}.agent.yaml"
            if apath.exists():
                out.append(str(apath.relative_to(REPO)))
            for sa in (m.get("sub_agents") or []):
                saref = sa.get("ref", "")
                sapath = REPO / "AgentsCatalogue" / "agents" / f"{saref}.agent.yaml"
                if sapath.exists():
                    out.append(str(sapath.relative_to(REPO)))
        return sorted(set(out))

    def _find_skill_for_symbol(self, skill_id: str) -> Optional[str]:
        """skill_id = 'symbol:<chemin>:<fn>' → skill_ref (ex. workspace/decoupe@v1).
        Scanne les yaml skills dont l'implémentation résout vers ce path."""
        import yaml
        target = skill_id[len("symbol:"):]  # "<chemin>:<fn>"
        skills_root = REPO / "AgentsCatalogue" / "skills"
        for y in skills_root.rglob("*.yaml"):
            try:
                data = yaml.safe_load(y.read_text()) or {}
            except Exception:
                continue
            impl = data.get("implementation") or {}
            fn = impl.get("function") if isinstance(impl, dict) else None
            if not fn:
                continue
            sym = python_translator.translate(fn)
            if sym and sym.path == target:
                return f"{y.parent.name}/{y.stem}"
        return None

    def _team_skill_symbol_ids(self, team_path: Path) -> List[str]:
        """id_data des symbols SKILLS de tous les membres de la team."""
        import yaml
        data = yaml.safe_load(team_path.read_text()) or {}
        skills: List[str] = []
        for m in data.get("members", []) or []:
            ref = m.get("ref", "")
            apath = REPO / "AgentsCatalogue" / "agents" / f"{ref}.agent.yaml"
            if not apath.exists():
                continue
            try:
                acfg = yaml.safe_load(apath.read_text()) or {}
            except Exception:
                continue
            skills.extend(self._collect_skills(acfg))
            for sa in (m.get("sub_agents") or []):
                saref = sa.get("ref", "")
                sapath = REPO / "AgentsCatalogue" / "agents" / f"{saref}.agent.yaml"
                if sapath.exists():
                    try:
                        skills.extend(self._collect_skills(
                            yaml.safe_load(sapath.read_text()) or {}))
                    except Exception:
                        pass
        out: List[str] = []
        for sk in sorted(set(skills)):
            sym = skill_symbol(sk)
            if sym is not None:
                out.append(f"symbol:{sym.path}")
        return sorted(set(out))

    def get_petri(self, team_path: Path) -> Optional[Dict[str, Any]]:
        """Lit le pétri en cache (passif) sans régénérer."""
        import yaml
        data = yaml.safe_load(team_path.read_text()) or {}
        return self.gen.get(0, f"petri:{data.get('name', team_path.stem)}")

    def get_agent_workflow(self, agent_path: Path,
                           force: bool = False) -> Optional[Dict[str, Any]]:
        """Workflow EXPANDU d'un agent via get_data_genere (kind=workflow).

        C'est la vérité exécutée par le FSM (expand_workflow déroule les
        skills). Invalidation par hash du yaml + contenu expandu."""
        import json as _json
        id_data = f"workflow:{agent_path}"
        params = _json.dumps({"agent_path": str(agent_path)})
        return self.get_data_genere(id_data, generator="workflow",
                                    params=params, project_id=0, force=force)

    def get_team_petri(self, team_path: Path,
                       force: bool = False) -> Optional[Dict[str, Any]]:
        """Pétri d'une team via get_data_genere : RÉCURSIF sur les symbols
        skills des membres (deps data_genere) + fichiers agents/team.

        C'est l'exemple canonique de la récursion : le pétri dépend de N
        symbols ; chaque symbol est résolu (validité + régénération si
        besoin) AVANT le calcul du hash du pétri."""
        import yaml
        import json as _json
        data = yaml.safe_load(team_path.read_text()) or {}
        team_name = data.get("name", team_path.stem)
        id_data = f"petri:{team_name}"
        # deps : chaque skill membre → spec {id, generator: symbol, params}
        deps = []
        for sk in sorted(self._team_skill_symbol_ids(team_path)):
            # sk = "symbol:<chemin_relatif>:<fonction>" → retrouve le skill yaml
            # sous AgentsCatalogue/skills/ (le générateur symbol le résout)
            deps.append({
                "id": sk, "generator": "symbol",
                "params": _json.dumps({"skill_id": sk}),
            })
        # params : team_path (le générateur petri s'en sert)
        params = _json.dumps({"team_path": str(team_path)})
        return self.get_data_genere(
            id_data, generator="petri", deps=deps, params=params,
            project_id=0, force=force)


# ── Registre des générateurs ──────────────────────────────────
# generator → callable(fe, id_data, project_id, params, force, deps).
# Chaque générateur produit la data_genere ; get_data_genere gère
# l'invalidation (inputs_hash) et la récursion sur les deps.
def _gen_symbol(fe, id_data, project_id=0, params="", force=False, deps=None):
    """Générateur d'un SYMBOL de skill. `params` = JSON {skill_id} (le id_data
    du symbol). Le skill_ref est résolu en scannant les yaml skills dont
    l'implémentation mène au path du symbol."""
    import json as _json
    spec = _json.loads(params) if params else {}
    skill_id = spec.get("skill_id", "") or id_data
    # skill_id = "symbol:<chemin>:<fn>" → retrouve le skill yaml correspondant
    skill_ref = fe._find_skill_for_symbol(skill_id)
    if not skill_ref:
        return None
    return fe.ensure_skill_symbol(skill_ref, force=force)


def _gen_workflow(fe, id_data, project_id=0, params="", force=False, deps=None):
    """Générateur du WORKFLOW EXPANDU d'un agent.

    `params` = JSON {agent_path} (chemin relatif du yaml agent). Charge le
    yaml, expand via expand_workflow (déroule les skills call → steps
    inline), stocke le workflow expandu en data_genere (kind=workflow).

    C'est la VÉRITÉ EXÉCUTÉE : le FSM exécute ce workflow expandu (pas le
    yaml brut). Le stocker permet au pétri de se générer depuis lui →
    cohérence analyse/exécution."""
    import yaml
    import json as _json
    from services.skill_manager import expand_workflow
    spec = _json.loads(params) if params else {}
    agent_path = REPO / spec.get("agent_path", "")
    if not agent_path.exists():
        return None
    try:
        cfg = yaml.safe_load(agent_path.read_text()) or {}
    except Exception:
        return None
    main_wf = ((cfg.get("entrypoints") or {}).get("main")
               or cfg.get("workflow") or cfg.get("pipeline"))
    if not isinstance(main_wf, dict):
        return None
    expanded = expand_workflow(main_wf)
    # hash : version + contenu du yaml + le workflow expandu lui-même
    h = hashlib.sha1()
    h.update(b"workflow-gen-v1")
    h.update(str(agent_path).encode())
    h.update(file_hash(agent_path).encode())
    h.update(_json.dumps(expanded, sort_keys=True, default=str).encode())
    ihash = h.hexdigest()
    existing = fe.gen.get(project_id, id_data)
    if existing and existing.get("inputs_hash") == ihash \
            and existing.get("status") == "valid" and not force:
        return existing
    fe.gen.upsert(
        project_id, id_data, name=agent_path.stem, kind="workflow",
        path=str(agent_path), ref_id=str(agent_path),
        value=_json.dumps(expanded, default=str),
        dependencies_json=[{"dep_ref": str(agent_path), "version": "",
                            "role": "agent"}],
        inputs_hash=ihash, status="valid", generation_mode="deterministic")
    fe.gen.add_dependency(project_id, id_data, str(agent_path), "", "agent")
    return {"workflow": id_data, "hash": ihash, "steps": len(expanded.get("steps", []))}


def _gen_team_petri(fe, id_data, project_id=0, params="", force=False,
                    deps=None):
    """Générateur du PÉTRI : résout team_path depuis params (JSON), construit
    le pétri global, vérifie l'invariant, enregistre value + deps.

    L'invalidation : inputs_hash = f(rules, hash des deps symbols résolues,
    hash des fichiers agents, version). Si la data est valid et a le même
    hash → retourne le cache (pas de re-calcul)."""
    import yaml
    from services.taskflow_petri import team_global_petri, \
        verify_team, save_petri_png
    import json as _json
    spec = _json.loads(params) if params else {}
    team_path = REPO / spec.get("team_path", "")
    if not team_path.exists():
        return None
    data = yaml.safe_load(team_path.read_text()) or {}
    team_name = data.get("name", team_path.stem)
    rules = [r for r in (data.get("supervisor_rules") or [])
             if isinstance(r, dict)]
    # ── inputs_hash : rules + deps symbols + fichiers agents + team ──
    h = hashlib.sha1()
    h.update(b"petri-gen-v1")
    h.update(_json.dumps(rules, sort_keys=True).encode())
    for d in sorted(deps or [], key=lambda x: x.get("id", "")):
        dep = fe.gen.get(project_id, d["id"])
        h.update((d["id"] + ":" +
                  (dep.get("inputs_hash", "") if dep else "")).encode())
    for m in sorted(fe._team_member_paths(team_path)):
        mp = REPO / m
        h.update(("agent:" + m + ":" +
                  (file_hash(mp) if mp.exists() else "")).encode())
    team_rel = str((REPO / team_path).resolve().relative_to(REPO))
    h.update(("team:" + team_rel + ":" + file_hash(REPO / team_rel)).encode())
    ihash = h.hexdigest()
    existing = fe.gen.get(project_id, id_data)
    if existing and existing.get("inputs_hash") == ihash \
            and existing.get("status") == "valid" and not force:
        return existing
    # ── construction réelle ──
    petri = team_global_petri(team_path, rules)
    verify = verify_team(team_path)
    render = petri.render()
    out_png = str(REPO / "docs" / "petri" / f"petri_{team_name}.png")
    try:
        save_petri_png(petri, out_png)
        png_file = True
    except Exception:
        out_png, png_file = "", False
    dep_list = [{"dep_ref": d["id"], "version": "", "role": "member"}
                for d in sorted(deps or [], key=lambda x: x.get("id", ""))]
    for m in sorted(fe._team_member_paths(team_path)):
        dep_list.append({"dep_ref": m, "version": "", "role": "agent"})
    dep_list.append({"dep_ref": team_rel, "version": "", "role": "team"})
    fe.gen.upsert(
        project_id, id_data, name=team_name, kind="petri",
        path=f"petri:{team_name}", ref_id=f"team:{team_name}",
        value=render, value_is_file=False,
        dependencies_json=dep_list,
        inputs_hash=ihash, status="valid", generation_mode="deterministic")
    for d in sorted(deps or [], key=lambda x: x.get("id", "")):
        fe.gen.add_dependency(project_id, id_data, d["id"], "", "member")
    for m in sorted(fe._team_member_paths(team_path)):
        fe.gen.add_dependency(project_id, id_data, m, "", "agent")
    fe.gen.add_dependency(project_id, id_data, team_rel, "", "team")
    if png_file:
        fe.gen.add_dependency(project_id, id_data, out_png, "", "reference")
    return {"petri": id_data, "verify": verify, "png": out_png or None}


_GENERATORS: Dict[str, Any] = {
    "symbol": _gen_symbol,
    "workflow": _gen_workflow,
    "petri": _gen_team_petri,
}


__all__ = ["FlowEngine", "TRANSLATOR_VERSION", "file_hash", "_GENERATORS"]
