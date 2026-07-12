#!/usr/bin/env python3
"""
ModelWeaver — Hard-check des contrats de modules/services.

Vérification statique + introspection (pas une preuve absolue, mais un contrôle
entrée/sortie réel), destinée au pré-commit / CI.

Pour chaque unité sous modules/* et services/* possédant _contract/interface.py :

  1. EXPOSES/EXPORTS résolvent
     - service : les routes déclarées == les routes réellement servies
       (ROUTES_SOURCE = "module:attribut"), dans les deux sens.
     - module  : chaque symbole EXPORTS est importable depuis le package.
  2. Les dépendances déclarées (CONSUMES) existent réellement dans l'unité source.
  3. Frontières (ast) : tout symbole d'une unité-source réellement utilisé dans le
     code est déclaré dans CONSUMES (pas d'import/usage « sauvage »).

Sortie : rapport PASS/FAIL par unité, code retour != 0 si au moins un échec.

Usage : python hardcheck/verify.py [--unit services/api]
"""
import sys
import os
import ast
import argparse
import importlib
import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


class Report:
    def __init__(self):
        self.errors = []
        self.checks = 0

    def ok(self, unit, msg):
        self.checks += 1
        print(f"  \033[32m✓\033[0m {unit}: {msg}")

    def fail(self, unit, msg):
        self.checks += 1
        self.errors.append((unit, msg))
        print(f"  \033[31m✗\033[0m {unit}: {msg}")


def _load_py(path: Path, mod_name: str):
    """Charge un fichier .py isolé comme module nommé."""
    spec = importlib.util.spec_from_file_location(mod_name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _import_source(name: str):
    """Importe une unité-source par son nom d'import (ex. 'gui_helper')."""
    return importlib.import_module(name)


def discover_units():
    units = []
    for base in ("modules", "services"):
        base_dir = REPO_ROOT / base
        if not base_dir.is_dir():
            continue
        for unit_dir in sorted(base_dir.iterdir()):
            if (unit_dir / "_contract" / "interface.py").exists():
                units.append(unit_dir)
    return units


def check_unit(unit_dir: Path, rep: Report):
    rel = str(unit_dir.relative_to(REPO_ROOT))
    contract = unit_dir / "_contract"
    iface = _load_py(contract / "interface.py", f"_iface_{unit_dir.name}")
    deps = None
    if (contract / "dependencies.py").exists():
        deps = _load_py(contract / "dependencies.py", f"_deps_{unit_dir.name}")

    kind = getattr(iface, "KIND", "module")

    # rendre le code de l'unité importable
    sys.path.insert(0, str(unit_dir))
    try:
        # ── 1. EXPOSES/EXPORTS résolvent ──
        if kind == "service":
            _check_service_routes(rel, unit_dir, iface, rep)
        else:
            _check_module_exports(rel, iface, rep)

        # ── 2 & 3. Dépendances ──
        if deps is not None:
            _check_dependencies(rel, unit_dir, deps, rep)
    finally:
        if str(unit_dir) in sys.path:
            sys.path.remove(str(unit_dir))


def _check_service_routes(rel, unit_dir, iface, rep):
    src = getattr(iface, "ROUTES_SOURCE", None)
    exposes = set(getattr(iface, "EXPOSES", {}).keys())
    if not src:
        rep.fail(rel, "service sans ROUTES_SOURCE")
        return
    mod_name, attr = src.split(":")
    try:
        mod = _load_py(unit_dir / f"{mod_name}.py", f"_impl_{unit_dir.name}_{mod_name}")
    except Exception as e:
        rep.fail(rel, f"import {mod_name}.py impossible: {e}")
        return
    real = set(getattr(mod, attr, {}).keys())
    if not real:
        rep.fail(rel, f"{src} vide ou introuvable")
        return
    missing = exposes - real          # déclarées mais non servies
    undeclared = real - exposes       # servies mais non déclarées
    if missing:
        rep.fail(rel, f"routes déclarées mais NON servies: {sorted(missing)}")
    if undeclared:
        rep.fail(rel, f"routes servies mais NON déclarées: {sorted(undeclared)}")
    if not missing and not undeclared:
        rep.ok(rel, f"{len(real)} routes: interface ↔ implémentation cohérentes")


def _check_module_exports(rel, iface, rep):
    exports = getattr(iface, "EXPORTS", [])
    pkg_name = getattr(iface, "NAME", None)
    try:
        pkg = importlib.import_module(pkg_name) if pkg_name else None
    except Exception as e:
        rep.fail(rel, f"import du package '{pkg_name}' impossible: {e}")
        return
    for sym in exports:
        if pkg is not None and hasattr(pkg, sym):
            continue
        rep.fail(rel, f"export déclaré introuvable: {sym}")
    else:
        if exports:
            rep.ok(rel, f"{len(exports)} exports résolus")


def _collect_alias_usages(unit_dir: Path, source_names):
    """Parcourt les .py de l'unité (hors _contract) et renvoie, par unité-source,
    l'ensemble des attributs réellement utilisés (import X as Y ; Y.attr)."""
    used = {name: set() for name in source_names}
    for py in unit_dir.rglob("*.py"):
        if "_contract" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        # alias -> nom d'unité-source
        alias_map = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name in source_names:
                        alias_map[a.asname or a.name] = a.name
            elif isinstance(node, ast.ImportFrom):
                if node.module in source_names:
                    for a in node.names:
                        # from src import foo -> usage direct de foo
                        used[node.module].add(a.name)
        # accès alias.attr
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                al = node.value.id
                if al in alias_map:
                    used[alias_map[al]].add(node.attr)
    return used


def _check_dependencies(rel, unit_dir, deps, rep):
    consumes = getattr(deps, "CONSUMES", {})
    # 2. les symboles déclarés existent dans l'unité source
    for src_name, symbols in consumes.items():
        try:
            src_mod = _import_source(src_name)
        except Exception as e:
            rep.fail(rel, f"dépendance '{src_name}' non importable: {e}")
            continue
        missing = [s for s in symbols if not hasattr(src_mod, s)]
        if missing:
            rep.fail(rel, f"symboles déclarés absents de '{src_name}': {missing}")
        else:
            rep.ok(rel, f"dépendance '{src_name}': {len(symbols)} symboles vérifiés")

    # 3. tout symbole réellement utilisé est déclaré (pas d'usage « sauvage »)
    used = _collect_alias_usages(unit_dir, set(consumes.keys()))
    for src_name, used_syms in used.items():
        declared = set(consumes.get(src_name, []))
        # on ignore les dunders/attributs privés de bas niveau non pertinents
        wild = {s for s in used_syms if not s.startswith("__")} - declared
        if wild:
            rep.fail(rel, f"usage NON déclaré de '{src_name}': {sorted(wild)} "
                          f"(ajouter à CONSUMES ou retirer l'appel)")
        elif declared:
            rep.ok(rel, f"usage de '{src_name}' conforme au contrat")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--unit", help="vérifier une seule unité (ex. services/api)")
    args = parser.parse_args()

    sys.path.insert(0, str(REPO_ROOT))

    units = discover_units()
    if args.unit:
        units = [REPO_ROOT / args.unit]
        if not (units[0] / "_contract" / "interface.py").exists():
            print(f"❌ pas de _contract/interface.py dans {args.unit}")
            sys.exit(2)

    if not units:
        print("Aucune unité avec _contract/ trouvée.")
        sys.exit(0)

    rep = Report()
    print(f"🔎 hard-check de {len(units)} unité(s)\n")
    for unit_dir in units:
        print(f"[{unit_dir.relative_to(REPO_ROOT)}]")
        try:
            check_unit(unit_dir, rep)
        except Exception as e:
            rep.fail(str(unit_dir.relative_to(REPO_ROOT)), f"exception: {e}")
        print()

    print("─" * 60)
    if rep.errors:
        print(f"\033[31mFAIL\033[0m — {len(rep.errors)} erreur(s) sur {rep.checks} vérifications")
        sys.exit(1)
    print(f"\033[32mPASS\033[0m — {rep.checks} vérifications OK")


if __name__ == "__main__":
    main()
