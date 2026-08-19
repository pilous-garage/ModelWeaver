"""env.path — le MODULE PATH commun de ModelWeaver.

UN SEUL langage de path pour tout le système, dotted (python-like) :

    modules.sqlite.services.add_service      module/fonction (réel)
    services.service_tick.list_all           service (réel)
    mw.svc                                   alias enregistré dans env.db

Règles de résolution (dans l'ordre) :
  1. Si l'expression est un ALIAS de env.db → on remplace par sa cible.
  2. Si l'expression contient '/' (ou est un chemin fs existant) → kind=fs.
  3. Sinon → IMPORT PYTHON : on descend segment par segment ; dès qu'un préfixe
     importe, le reste est des attributs (fonctions, classes). Le nominal:
     `a.b.c.fn` = module `a.b.c`, attribut `fn`.
  4. L'annuaire env.path sert de registre/découverte, PAS de contrainte :
     un module non déclaré reste résolvable (l'import est la vérité).

Toute couche qui veut appeler une fonction externe passe PAR ICI (ou par
modules.sqlite.modules.call() qui annote/resolve de la même façon). L'objectif
: un chemin, une grammaire, nulle part un import "à la main" pour le cross-appel.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Any, Dict, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent

# Racines par défaut — déclarées dans env.db à l'init (register_default_paths).
DEFAULT_ROOTS = [
    ("module", "modules", "MODULES_ROOT", "chemin python réel (import)"),
    ("service", "services", "SERVICES_ROOT", "services métier de ModelWeaver"),
    ("api", "services.api.handlers", "API_HANDLERS_ROOT",
     "handlers HTTP (routes op_*)"),
    ("skills", "AgentsCatalogue.skills", "SKILLS_ROOT", "skills des agents"),
    ("data", "", "DATA_ROOT", "données locales (catalogue, robot…)"),
    ("db", "", "DB_ROOT", "bases sqlite (~/.modelweaver)"),
    ("domains", "modules.sqlite", "DOMAINS_ROOT", "domaines sqlite"),
]


def _ensure_repo_path() -> None:
    p = str(REPO_ROOT)
    if p not in sys.path:
        sys.path.insert(0, p)


def resolve(expr: str, d=None) -> Dict[str, Any]:
    """Résout une expression de path → {kind, path, obj, module?, error?}.

    kind ∈ {module, func, fs, error}.
    """
    _ensure_repo_path()
    expr = (expr or "").strip()
    if not expr:
        return {"kind": "error", "error": "expression vide"}

    # 1. alias en base ? (préfixe le plus long d'abord)
    if d is not None:
        from modules.sqlite.env import read as R
        parts = expr.split(".")
        for i in range(len(parts), 0, -1):
            target = R.resolve_alias(d, ".".join(parts[:i]))
            if target:
                resto = parts[i:]
                expr = ".".join([target] + resto) if resto else target
                break

    # 2. chemin fs ?
    g = Path(expr)
    if "/" in expr or expr.startswith("~") or g.exists():
        return {"kind": "fs", "path": expr, "obj": g}

    # 3. import python, préfixe le plus long d'abord
    parts = expr.split(".")
    for i in range(len(parts), 0, -1):
        try:
            mod = importlib.import_module(".".join(parts[:i]))
        except ImportError:
            continue
        obj: Any = mod
        kind = "module"
        prefix = ".".join(parts[:i])
        attrs = parts[i:]
        for a in attrs:
            nxt = getattr(obj, a, None)
            if nxt is None:
                try:  # sous-module probable (package sans __init__ exportant)
                    nxt = importlib.import_module(f"{prefix}.{a}")
                except ImportError:
                    return {"kind": "error", "path": expr,
                            "error": f"{expr}: attribut '{a}' introuvable"}
            obj = nxt
            if callable(obj) and a == attrs[-1]:
                kind = "func"
        return {"kind": kind, "path": expr, "obj": obj,
                "module": ".".join(parts[:i])}
    return {"kind": "error", "path": expr, "error": f"introuvable: {expr}"}


def call(expr: str, *args, d=None, **kwargs) -> Any:
    """Appelle la fonction au bout du path : env.path.call('services.x.fn', a)."""
    r = resolve(expr, d=d)
    if r["kind"] == "error":
        raise LookupError(r["error"])
    if r["kind"] == "func":
        return r["obj"](*args, **kwargs)
    raise TypeError(f"{expr} n'est pas une fonction (kind={r['kind']})")


def register_default_paths(d) -> Dict[str, Any]:
    """Déclare les racines par défaut dans l'annuaire env.path (idempotent)."""
    from modules.sqlite.env import write as W
    done = []
    for entry_type, ref, name, desc in DEFAULT_ROOTS:
        W.register_path(d, name, entry_type=entry_type, parent="",
                        ref=ref, description=desc)
        done.append(name)
    return {"ok": True, "roots": done}


def discover(roots=None) -> Dict[str, Any]:
    """Découverte récursive des packages sous les racines → {path, type}."""
    _ensure_repo_path()
    out = []
    roots = roots or [("modules", "module"), ("services", "service"),
                      ("modules.sqlite", "domain")]
    for root, rtype in roots:
        base = REPO_ROOT / root.replace(".", "/")
        if not base.is_dir():
            continue
        for p in sorted(base.rglob("*.py")):
            rel = p.relative_to(REPO_ROOT)
            parts = list(rel.parts)
            if parts[-1] == "__init__.py":
                parts = parts[:-1]
            else:
                parts[-1] = parts[-1][:-3]
            if not parts or "__pycache__" in parts or parts[0].startswith("source-"):
                continue
            out.append({"path": ".".join(parts), "type": rtype})
    return {"ok": True, "nodes": out}