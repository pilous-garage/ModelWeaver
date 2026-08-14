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
    """Moteur de génération des data_genere (skills + supervisor)."""

    def __init__(self, db_path: Optional[Path] = None, auto_flush: bool = True):
        self.gen = GenereCatalogue(db_path=db_path, auto_flush=auto_flush)

    def close(self) -> None:
        try:
            self.gen.flush()
        except Exception:
            pass

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
        """Construit le pétri GLOBAL d'une team en tant que data_genere
        passif (kind=petri) : invalidation par inputs_hash.

        Le pétri dépend des symbols des agents (+ superviseur). S'ils n'ont
        pas changé (inputs_hash identique), on renvoie le pétri en cache —
        zéro re-calcul. Sinon on régénère et on stocke.
        """
        import yaml
        from services.taskflow_petri import team_global_petri, \
            verify_team, save_petri_png

        data = yaml.safe_load(team_path.read_text()) or {}
        team_name = data.get("name", team_path.stem)
        rules = [r for r in (data.get("supervisor_rules") or []) if isinstance(r, dict)]
        project_id = 0
        id_data = f"petri:{team_name}"

        # ── inputs_hash : version pétri + rules + hash des symbols membres ──
        # 1. assure les symbols des agents (skills) d'abord
        self.ensure_team_symbols(team_path)
        # 2. hash : rules + symbols skills des membres (le contenu réel)
        skill_ids = self._team_skill_symbol_ids(team_path)
        h = hashlib.sha1()
        h.update(b"petri-gen-v1")
        h.update(json.dumps(rules, sort_keys=True).encode())
        for sid in sorted(skill_ids):
            row = self.gen.get(project_id, sid)
            h.update((sid + ":" + (row.get("inputs_hash", "") if row else "")).encode())
        # + hash des agents (structure du workflow)
        for mpath in sorted(self._team_member_paths(team_path)):
            mp = REPO / mpath
            h.update(("agent:" + mpath + ":" +
                      (file_hash(mp) if mp.exists() else "")).encode())
        ihash = h.hexdigest()

        existing = self.gen.get(project_id, id_data)
        if existing and existing.get("inputs_hash") == ihash and not force:
            # déjà valide → renvoie tel quel
            return {"petri": id_data, "cached": True, "hash": ihash,
                    "symbols": skill_ids}

        # ── construction réelle ──
        petri = team_global_petri(team_path, rules)
        verify = verify_team(team_path)
        # rendu texte + PNG (value_is_file)
        render = petri.render()
        out_png = str(REPO / "docs" / "petri" / f"petri_{team_name}.png")
        try:
            save_petri_png(petri, out_png)
            png_file = True
        except Exception:
            out_png, png_file = "", False

        # dépendances du petri : symbols skills + FICHIERS AGENTS (un agent
        # modifié → stale propagé au petri) + fichier team + PNG.
        member_paths = self._team_member_paths(team_path)
        dep_list = [{"dep_ref": s, "version": "", "role": "member"}
                    for s in sorted(skill_ids)]
        dep_list += [{"dep_ref": m, "version": "", "role": "agent"}
                     for m in sorted(member_paths)]
        dep_list.append({"dep_ref": str((REPO / team_path).resolve().relative_to(REPO)),
                         "version": "", "role": "team"})
        self.gen.upsert(
            project_id, id_data, name=team_name, kind="petri",
            path=f"petri:{team_name}", ref_id=f"team:{team_name}",
            value=render, value_is_file=False,
            dependencies_json=dep_list,
            inputs_hash=ihash, status="valid", generation_mode="deterministic")
        for s in sorted(skill_ids):
            self.gen.add_dependency(project_id, id_data, s, "", "member")
        for m in sorted(member_paths):
            self.gen.add_dependency(project_id, id_data, m, "", "agent")
        team_rel = str((REPO / team_path).resolve().relative_to(REPO))
        self.gen.add_dependency(project_id, id_data, team_rel, "", "team")
        if png_file:
            self.gen.add_dependency(project_id, id_data, out_png, "", "reference")

        return {"petri": id_data, "cached": False, "hash": ihash,
                "symbols": skill_ids, "verify": verify,
                "png": out_png or None}

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


__all__ = ["FlowEngine", "TRANSLATOR_VERSION", "file_hash"]
