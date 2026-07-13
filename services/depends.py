#!/usr/bin/env python3
"""Vérification des dépendances des modules/services ModelWeaver.

On n'impose QUE Python + SQLite. Tout le reste (pip packages, binaires système)
est DÉCLARÉ par unité via ``DEPENDS`` dans son ``_contract/interface.py`` et vérifié
ici. Si une dépendance manque, l'unité qui en dépend ne peut pas fonctionner : le
daemon le signale (route ``deps/check``) et le superviseur ne la lance pas
(l'entrypoint s'auto-vérifie et quitte proprement avec un message clair).

Format ``DEPENDS`` (liste de specs) :
    {"pip": "litellm"}            -> package Python importable
    {"pip": "open_webui", "min": "0.1"}
    {"bin": "docker"}             -> binaire présent dans le PATH
    {"bin": "git", "min": "2.0"}  (version optionnelle, non strictement vérifiée)
"""
import importlib
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional


def check_python_dep(name: str, minimum: Optional[str] = None) -> Dict[str, Any]:
    try:
        importlib.import_module(name)
        return {"kind": "pip", "name": name, "present": True, "version": None,
                "min": minimum, "ok": True}
    except Exception:
        return {"kind": "pip", "name": name, "present": False, "version": None,
                "min": minimum, "ok": False}


def check_binary_dep(name: str, minimum: Optional[str] = None) -> Dict[str, Any]:
    present = shutil.which(name) is not None
    return {"kind": "bin", "name": name, "present": present, "version": None,
            "min": minimum, "ok": present}


def check_dependency(spec: Dict[str, Any]) -> Dict[str, Any]:
    if spec.get("kind") == "bin" or "bin" in spec:
        return check_binary_dep(spec.get("bin") or spec.get("name"), spec.get("min"))
    return check_python_dep(spec.get("pip") or spec.get("name"), spec.get("min"))


def read_unit_depends(unit_dir: Path) -> List[Dict[str, Any]]:
    """Lit DEPENDS depuis _contract/interface.py d'une unité (peut être vide)."""
    iface = unit_dir / "_contract" / "interface.py"
    if not iface.exists():
        return []
    ns: Dict[str, Any] = {}
    try:
        code = compile(iface.read_text(), str(iface), "exec")
        exec(code, ns)
    except Exception:
        return []
    return ns.get("DEPENDS", []) or []


def check_unit_dependencies(unit_dir: Path) -> Dict[str, Any]:
    """Retourne l'état de dépendances d'une unité (module ou service)."""
    name = None
    kind = None
    iface = unit_dir / "_contract" / "interface.py"
    if iface.exists():
        ns: Dict[str, Any] = {}
        try:
            exec(compile(iface.read_text(), str(iface), "exec"), ns)
            name = ns.get("NAME")
            kind = ns.get("KIND")
        except Exception:
            pass
    specs = read_unit_depends(unit_dir)
    results = [check_dependency(s) for s in specs]
    ok = all(r["ok"] for r in results)
    return {
        "name": name,
        "kind": kind,
        "path": str(unit_dir),
        "dependencies": results,
        "all_present": ok,
    }


def check_all_units(repo_root: Path) -> Dict[str, Any]:
    """Vérifie modules/ et services/."""
    out: Dict[str, Any] = {"modules": [], "services": [], "all_present": True}
    for base in ("modules", "services"):
        base_dir = repo_root / base
        if not base_dir.is_dir():
            continue
        for unit_dir in sorted(base_dir.iterdir()):
            iface = unit_dir / "_contract" / "interface.py"
            if iface.exists():
                res = check_unit_dependencies(unit_dir)
                out[base].append(res)
                if not res["all_present"]:
                    out["all_present"] = False
    return out


def require_deps(unit_dir: Path) -> bool:
    """Pour un entrypoint de service : vérifie ses DEPENDS et quitte proprement
    (code 3) si une dépendance manque. Retourne True si tout est présent."""
    res = check_unit_dependencies(unit_dir)
    missing = [r for r in res["dependencies"] if not r["ok"]]
    if missing:
        import sys
        sys.stderr.write(
            f"[deps] service '{res['name']}' : dépendances manquantes -> "
            + ", ".join(f"{r['kind']}:{r['name']}" for r in missing)
            + " (service non démarré)\n")
        return False
    return True


if __name__ == "__main__":
    import json
    root = Path(__file__).resolve().parent.parent
    print(json.dumps(check_all_units(root), indent=2))
