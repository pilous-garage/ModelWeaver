"""Registre des bibliothèques de fonctions (V0.7 — Agent Sandbox).

Arborescence : AgentsCatalogue/lib/{domaine}/{fichier}.py
Référence qualifiée (relative à lib/) : system.file.read_file
  -> module AgentsCatalogue.lib.system.file, fonction read_file

Chaque module liste ses fonctions-skills dans __skills__ = [...].
Les fonctions ont la signature (inputs: dict, ws: str) -> dict.

Résolution pour le hover IDE : resolve("system.file.read_file")
renvoie {source, module, func, file}. Aucun dispatch OS/archi (YAGNI) :
les fonctions sont codées proprement et restent portables.
"""

import importlib
import inspect
from pathlib import Path
from typing import Dict, List, Optional

_LIB_ROOT = Path(__file__).resolve().parent

# ref qualifié (ex: "system.file.read_file") -> callable
_REGISTRY: Dict[str, callable] = {}
# ref qualifié -> source de la fonction (pour le hover)
_SOURCES: Dict[str, str] = {}
# nom legacy "_exec_xxx" -> ref qualifié (rétro-compat YAML function:)
_ALIASES: Dict[str, str] = {}

_SCAN_DONE = False


def _modname(rel_py: Path) -> str:
    parts = rel_py.with_suffix("").parts
    return ".".join(parts)


def scan() -> None:
    global _SCAN_DONE
    if _SCAN_DONE:
        return
    for py in sorted(_LIB_ROOT.rglob("*.py")):
        rel = py.relative_to(_LIB_ROOT)
        if rel.name == "__init__.py":
            continue
        module = _modname(rel)
        try:
            mod = importlib.import_module(f"AgentsCatalogue.lib.{module}")
        except Exception as e:
            print(f"  ⚠️ lib {module}: import échoué ({e})")
            continue
        skills = getattr(mod, "__skills__", []) or []
        for name in skills:
            func = getattr(mod, name, None)
            if not callable(func):
                continue
            key = f"{module}.{name}"
            _REGISTRY[key] = func
            try:
                _SOURCES[key] = inspect.getsource(func)
            except (OSError, TypeError):
                _SOURCES[key] = ""
            # alias legacy "_exec_<name>" -> key (pour YAML function:)
            _ALIASES[f"_exec_{name}"] = key
    _SCAN_DONE = True


def get_func(ref: str) -> Optional[callable]:
    scan()
    if ref in _REGISTRY:
        return _REGISTRY[ref]
    if ref in _ALIASES:
        return _REGISTRY.get(_ALIASES[ref])
    return None


def resolve(ref: str) -> dict:
    """Résout une référence qualifiée -> {source, module, func, file, found}."""
    scan()
    key = ref if ref in _REGISTRY else _ALIASES.get(ref)
    if not key or key not in _REGISTRY:
        return {"found": False, "ref": ref}
    func = _REGISTRY[key]
    mod = inspect.getmodule(func)
    return {
        "found": True,
        "ref": ref,
        "qualified": key,
        "module": key.rsplit(".", 1)[0],
        "func": key.rsplit(".", 1)[1],
        "source": _SOURCES.get(key, ""),
        "file": str(Path(mod.__file__).relative_to(_LIB_ROOT)) if mod else "",
    }


def list_all() -> List[dict]:
    scan()
    out = []
    for key in sorted(_REGISTRY):
        out.append({
            "ref": key,
            "module": key.rsplit(".", 1)[0],
            "func": key.rsplit(".", 1)[1],
            "doc": (inspect.getdoc(_REGISTRY[key]) or "").split("\n")[0],
        })
    return out


def call(ref: str, inputs: dict, ws: str) -> dict:
    """Appelle une fonction de la librairie (signature run(inputs, ws))."""
    func = get_func(ref)
    if not func:
        raise KeyError(f"fonction lib introuvable: {ref}")
    return func(inputs, ws)
