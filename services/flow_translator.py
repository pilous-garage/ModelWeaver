"""flow_translator — analyse statique Python des skills → symbol + contract.

Traduit une fonction skill (ex. `workspacedb.taskflow.decoupe`) en un `symbol`
du catalogue genere avec son CONTRACT in/out : quelles opérations BDD elle fait
(creates / transitions / releases / reads), ses dépendances (fichiers importés,
schéma SQL), et son mode de génération.

La grammaire universelle du symbol est `fichier.fonction` ou
`fichier.classe.fonction` — chaque langage a un traducteur (python, yaml, sql…).
Ici : traducteur python basé sur `ast`.

Usage:
    from services.flow_translator import python_translator, resolve_skill_fn
    sym = python_translator.translate("workspacedb.taskflow.decoupe")
    print(sym.contract)   # {"reads": [...], "creates": [...], ...}
"""

from __future__ import annotations

import ast
import importlib.util
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO = Path(__file__).resolve().parent.parent

# Racines de modules skill → chemins sur disque.
_LIB = REPO / "AgentsCatalogue" / "lib"
MODULE_ROOTS = {
    "workspacedb": _LIB / "workspacedb",
    "workspace": _LIB / "workspace",
    "file": _LIB / "file",
    "git": _LIB / "git",
    "shell": _LIB / "shell",
    "system": _LIB / "system",
    "host": _LIB / "host",
    "net": _LIB / "net",
    "agent": _LIB / "agent",
    "context": _LIB / "context",
    "memory": _LIB / "memory",
    "util": _LIB / "util",
    "workflow": _LIB / "workflow",
    "llm": _LIB / "llm",
    "project": _LIB / "project",
    # git.git_commit → git_ops.py (fichier à la racine de lib)
    "git_ops": _LIB,
}

# Modules de la racine lib résolus en fichiers directs (module.sans_dot → fichier).
_LIB_FLAT = {p.stem: p for p in _LIB.glob("*.py") if not p.name.startswith("_")}

# Règles de mapping module → fichier de la racine lib.
# Les skills `git/git_commit@v1` déclarent `function: git.git_commit`, or les
# fonctions vivent dans `lib/git_ops.py`. module "git" → fichier "git_ops".
MODULE_ALIASES = {
    "git": lambda fname: _LIB / "git_ops.py",
}

# Méthodes repo SQLite → type d'opération BDD.
# Sur `sc.sub_tasks.<meth>` / `sc.tasks.<meth>`.
REPO_OPS: Dict[str, str] = {
    # CREATE → produit une sub_task/task (nouveau jeton).
    "create": "create",
    # TRANSITION → change l'état (le status exact est lu dans les args).
    "set_status": "transition",
    "claim": "transition",
    # RELEASE → remet en unattributed (échec).
    "release": "release",
    # FINALISE → groupe clos (terminal).
    "mark_supervised": "finalise",
    # WAIT → passe en waiting_dependencies (deps non satisfaites).
    "waiting_dependencies": "wait",
    # DEPENDENCY → lien child→parent.
    "add_dependency": "dependency",
    # READS → lecture seule.
    "get": "read",
    "list_for_task": "read",
    "list_by_type": "read",
    "list_by_team_status": "read",
    "list_assigned_to": "read",
    "list": "read",
    "get_parents": "read",
    "get_children": "read",
    "get_reports": "read",
    "add_report": "write",
    "update": "update",
    "dependencies_satisfied": "check",
}

# Objets porteurs de repo (prefixes d'appels).
REPO_OWNERS = ("sub_tasks", "tasks", "ask", "sub_task_dependencies")

# Noms locaux bannis (dicts/paramètres courants) — faux positifs de reads.
BANNED_OWNERS = {
    "inputs", "task", "t", "tsk", "r", "row", "st", "ns", "exp", "cur",
    "prev", "dep", "p", "cfg", "config", "self", "s",
}

# Status finaux SQLite (transitions) reconnus.
KNOWN_STATUS = ("done", "cancelled", "supervised", "unattributed",
                "attributed", "doing", "waiting_dependencies", "todo")


class Symbol:
    """Un symbol analysé : fichier.fonction + contract in/out."""

    def __init__(self, ref: str, path: str, lang: str, kind: str,
                 contract: Dict[str, Any], dependencies: List[Dict[str, str]],
                 generation_mode: str = "deterministic"):
        self.ref = ref
        self.path = path
        self.lang = lang
        self.kind = kind
        self.contract = contract
        self.dependencies = dependencies
        self.generation_mode = generation_mode

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref, "path": self.path, "lang": self.lang,
            "kind": self.kind, "contract": self.contract,
            "dependencies": self.dependencies,
            "generation_mode": self.generation_mode,
        }


class PythonTranslator:
    """Traducteur Python : parse le .py d'un module skill, extrait pour une
    fonction donnée les opérations BDD + les imports (dépendances)."""

    def _resolve(self, dotted: str) -> Optional[Path]:
        """'workspacedb.taskflow' → Path du .py."""
        parts = dotted.split(".")
        root = parts[0]
        base = MODULE_ROOTS.get(root)
        if base is None:
            return None
        rel = parts[1:]
        if not rel:
            # module racine lui-même → __init__.py
            return (base / "__init__.py") if (base / "__init__.py").exists() else None
        # dernier segment = module, les autres = dirs
        fname = rel[-1]
        p = base.joinpath(*rel[:-1], f"{fname}.py") if len(rel) > 1 \
            else base / f"{fname}.py"
        if p.exists():
            return p
        # fallback : fichier plat de la racine lib (ex. git.git_commit → git_ops.py)
        flat = _LIB_FLAT.get(fname)
        if flat is not None and len(rel) == 1:
            return flat
        return None

    def _walk_calls(self, tree: ast.AST) -> List[ast.Call]:
        return [n for n in ast.walk(tree) if isinstance(n, ast.Call)]

    def _call_info(self, call: ast.Call) -> Optional[Dict[str, Any]]:
        """Remonte un appel : owner.method(args) → infos BDD."""
        f = call.func
        if not isinstance(f, ast.Attribute):
            return None
        parts: List[str] = []
        node = f
        while isinstance(node, ast.Attribute):
            parts.append(node.attr)
            node = node.value
        if isinstance(node, ast.Name):
            parts.append(node.id)
        elif isinstance(node, ast.Call):
            # sc.for_workspace(...).sub_tasks.create → owner résolu à la main
            pass
        parts = list(reversed(parts))
        if len(parts) < 2:
            return None
        owner, meth = parts[-2], parts[-1]
        if owner in BANNED_OWNERS:
            return None
        if meth not in REPO_OPS:
            return None
        op = REPO_OPS.get(meth, meth)
        # status passé en argument pour set_status/claim (ex. "done").
        status = ""
        if meth == "set_status":
            for a in call.args:
                if isinstance(a, ast.Constant) and isinstance(a.value, str) \
                        and a.value in KNOWN_STATUS:
                    status = a.value
                    break
        return {"owner": owner, "meth": meth, "op": op, "status": status}

    def _extract_contract(self, fn: ast.FunctionDef) -> Dict[str, Any]:
        contract: Dict[str, Any] = {
            "reads": [], "creates": [], "transitions": [],
            "releases": [], "finalise": [], "waits": [],
            "dependencies": [], "writes": [], "checks": [],
        }
        for call in self._walk_calls(fn):
            info = self._call_info(call)
            if info is None:
                continue
            key = {
                "read": "reads", "create": "creates",
                "transition": "transitions", "release": "releases",
                "finalise": "finalise", "wait": "waits",
                "dependency": "dependencies", "write": "writes",
                "check": "checks", "update": "writes",
            }.get(info["op"])
            if key is None:
                continue
            label = f"{info['owner']}.{info['meth']}"
            if info["status"]:
                label += f" -> {info['status']}"
            if label not in contract[key]:
                contract[key].append(label)
        return contract

    def _extract_imports(self, tree: ast.AST,
                         fn: ast.FunctionDef) -> List[Dict[str, str]]:
        deps: List[Dict[str, str]] = []
        seen = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    deps.append({"dep_ref": alias.name, "role": "import"})
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for alias in node.names:
                    ref = f"{mod}.{alias.name}" if mod else alias.name
                    deps.append({"dep_ref": ref, "role": "import"})
        # fichiers du repo touchés (via les modules analysés) + schéma SQL.
        for d in deps:
            p = self._resolve(d["dep_ref"].split(".")[0] + "."
                              + ".".join(d["dep_ref"].split(".")[1:]))
            if p:
                d["file"] = str(p.relative_to(REPO))
        # déduplique en gardant l'ordre
        out = []
        for d in deps:
            k = (d.get("dep_ref", ""), d.get("role", ""))
            if k not in seen:
                seen.add(k)
                out.append(d)
        return out

    def translate(self, dotted: str) -> Optional[Symbol]:
        """Analyse 'module.fonction' → Symbol (ou None si introuvable).

        Plusieurs interprétations tentées (le module déclaré dans le yaml
        skill n'est pas toujours un chemin exact) :
          - 'a.b.fn' → fonction fn de a.b (chemin complet) ;
          - 'a.fn'   → fonction fn du module a ;
          - 'git.git_commit' → fonction git_commit du fichier git_ops.py
            (alias module → fichier de la racine lib).
        """
        parts = dotted.split(".")
        if len(parts) < 2:
            return None
        fn_name = parts[-1]
        # 1) interprétation standard : tout sauf le dernier = module
        mod_dotted = ".".join(parts[:-1])
        p = self._resolve(mod_dotted)
        # 2) interprétation "racine alias" : root.module → fichier flat/alias
        if p is None:
            root, fname = parts[0], parts[1]
            alias = MODULE_ALIASES.get(root)
            cand = alias(fname) if alias is not None else _LIB_FLAT.get(fname)
            if cand is not None and cand.exists():
                p = cand
        if p is None:
            return None
        try:
            tree = ast.parse(p.read_text())
        except SyntaxError:
            tree = None
        # trouve la fonction (module ou méthode de classe)
        fn: Optional[ast.FunctionDef] = None
        if tree is not None:
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                    fn = node
                    break
        # fallback alias/flat si la fonction n'est pas dans le module résolu
        # (ex. git.git_commit : module 'git' → lib/git/__init__.py sans la
        # fonction, la vraie est dans git_ops.py).
        if fn is None and len(parts) >= 2:
            root, fname = parts[0], parts[1]
            alias = MODULE_ALIASES.get(root)
            cand = alias(fname) if alias is not None else _LIB_FLAT.get(fname)
            if cand is not None and cand.exists():
                p = cand
                try:
                    tree = ast.parse(p.read_text())
                except SyntaxError:
                    tree = None
                for node in ast.walk(tree) if tree is not None else ():
                    if isinstance(node, ast.FunctionDef) and node.name == fn_name:
                        fn = node
                        break
        if fn is None:
            return None
        rel = str(p.relative_to(REPO))
        ref = f"{rel}:{fn_name}"
        contract = self._extract_contract(fn)
        deps = self._extract_imports(tree, fn)
        return Symbol(ref=ref, path=f"{rel}:{fn_name}", lang="python",
                      kind="function", contract=contract,
                      dependencies=deps)


python_translator = PythonTranslator()


def resolve_skill_fn(skill_yaml: Dict[str, Any]) -> Optional[str]:
    """Lit l'implémentation d'un skill.yaml → 'workspacedb.taskflow.decoupe'."""
    impl = skill_yaml.get("implementation") or {}
    if isinstance(impl, dict):
        return impl.get("function") or impl.get("fn")
    return None


def skill_symbol(skill_ref: str) -> Optional[Symbol]:
    """skill_ref ex. 'workspace/decoupe@v1' → Symbol analysé (ou None)."""
    import yaml
    fname = skill_ref.replace("@", "@").split("@")[0] + "@v1.skill.yaml"
    # gère le nom complet : workspace/decoupe@v1 → workspace/decoupe@v1.skill.yaml
    base = skill_ref.split("@")[0]
    path = REPO / "AgentsCatalogue" / "skills" / f"{base}@v1.skill.yaml"
    if not path.exists():
        # fallback : chercher par suffixe
        hits = list((REPO / "AgentsCatalogue" / "skills").glob(f"{base}*.yaml"))
        if not hits:
            return None
        path = hits[0]
    try:
        data = yaml.safe_load(path.read_text()) or {}
    except Exception:
        return None
    fn = resolve_skill_fn(data)
    if not fn:
        return None
    return python_translator.translate(fn)


def supervisor_symbol(rules: Optional[List[Dict[str, Any]]] = None,
                      team_name: str = "team") -> Symbol:
    """Construit le symbol du SUPERVISOR (sans LLM) résolu par ses RÈGLES.

    Le superviseur est un service (`task_supervisor/service.py`) dont le
    comportement est piloté par les règles du manifest team :
      (in_type, in_tag) → (out_type, out_tag).

    Le contract est RÉSOLU depuis le code de `supervise_team` :
      - release_dependencies : set_status → unattributed (deps satisfaites) ;
      - apply_rules : sub_tasks done/cancelled → create la suivante
        (règle out_type) + mark_supervised ;
      - composants de découpe (children) → mark_supervised direct ;
      - finalize_tasks : tâches closes → tasks.update → supervised + respond.
    Les types PRODUITS (unattributed) = les out_type des règles + respond.
    """
    fn = "services/task_supervisor/service.py:TaskSupervisor.supervise_team"
    contract: Dict[str, Any] = {
        "reads": ["sub_tasks.list_by_team_status", "sub_tasks.dependencies_satisfied",
                  "sub_tasks.get_children", "rules.find_rule", "tasks.get",
                  "sub_tasks.list_for_task"],
        "creates": [],          # rempli par les règles + respond
        "transitions": ["sub_tasks.set_status -> unattributed"],
        "releases": [],
        "finalise": ["sub_tasks.mark_supervised", "tasks.update -> supervised"],
        "waits": [],
        "dependencies": ["sub_tasks.add_dependency"],
        "writes": ["tasks.update -> supervised"],
        "checks": [],
        "rules": [],
    }
    for r in rules or []:
        it, itag = r.get("in_type", ""), r.get("in_tag", "")
        ot, otag = r.get("out_type", ""), r.get("out_tag", "")
        contract["rules"].append({"in_type": it, "in_tag": itag,
                                  "out_type": ot, "out_tag": otag})
        if ot:
            contract["creates"].append(f"sub_tasks.create ({it}+{itag} → {ot})")
    if "sub_tasks.create (respond)" not in contract["creates"]:
        contract["creates"].append("sub_tasks.create (respond)")
    deps = [
        {"dep_ref": "services/task_supervisor/service.py", "role": "implementation"},
        {"dep_ref": "modules/sql/workspace.py", "role": "schema"},
        {"dep_ref": "modules/sql/workspace_schema.sql", "role": "schema"},
    ]
    return Symbol(ref=f"supervisor:{team_name}",
                  path=f"supervisor:{team_name}", lang="python",
                  kind="supervisor", contract=contract, dependencies=deps)


__all__ = ["Symbol", "PythonTranslator", "python_translator",
           "resolve_skill_fn", "skill_symbol", "supervisor_symbol",
           "REPO", "MODULE_ROOTS"]
