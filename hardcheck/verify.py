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
    # Unités foundation à la racine (nom d'import historique préservé, ex. `sql`).
    for top in ("sql",):
        unit_dir = REPO_ROOT / top
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
        if kind == "service" and getattr(iface, "ROUTES_SOURCE", None):
            _check_service_routes(rel, unit_dir, iface, rep)
        elif kind == "service":
            _check_service_entrypoint(rel, unit_dir, iface, rep)
        else:
            _check_module_exports(rel, iface, rep)

        # ── 2 & 3. Dépendances ──
        if deps is not None:
            _check_dependencies(rel, unit_dir, deps, rep)
    finally:
        if str(unit_dir) in sys.path:
            sys.path.remove(str(unit_dir))


def _check_service_entrypoint(rel, unit_dir, iface, rep):
    """Service runnable (worker/watcher) : vérifie que le fichier entrypoint se
    charge sans erreur. La fonction lancée (RUNS) est vérifiée via CONSUMES."""
    entry = getattr(iface, "ENTRYPOINT", "service.py")
    path = unit_dir / entry
    if not path.exists():
        rep.fail(rel, f"entrypoint introuvable: {entry}")
        return
    try:
        _load_py(path, f"_svc_{unit_dir.name}_{path.stem}")
        rep.ok(rel, f"entrypoint '{entry}' se charge sans erreur")
    except Exception as e:
        rep.fail(rel, f"entrypoint '{entry}' échoue à l'import: {e}")


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
    target = getattr(iface, "MODULE", None) or getattr(iface, "NAME", None)
    if not target:
        rep.fail(rel, "module sans MODULE/NAME dans interface")
        return
    try:
        mod = importlib.import_module(target)
    except Exception as e:
        rep.fail(rel, f"import du module '{target}' impossible: {e}")
        return
    missing = [sym for sym in exports if not hasattr(mod, sym)]
    if missing:
        rep.fail(rel, f"exports déclarés introuvables dans '{target}': {missing}")
    elif exports:
        rep.ok(rel, f"{len(exports)} export(s) résolu(s) depuis '{target}'")
    else:
        rep.ok(rel, f"module '{target}' importable (aucun export déclaré)")


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


def _project_imports(unit_dir: Path, self_prefix: str):
    """Tous les imports vers des unités-projet (modules./sql./services.), hors
    soi-même. Retourne dict module_path -> set(symboles) ('*' si module entier)."""
    prefixes = ("modules.", "sql.", "services.")
    found = {}
    for py in unit_dir.rglob("*.py"):
        if "_contract" in py.parts:
            continue
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                if node.module.startswith(prefixes):
                    found.setdefault(node.module, set()).update(a.name for a in node.names)
            elif isinstance(node, ast.Import):
                for a in node.names:
                    if a.name.startswith(prefixes):
                        found.setdefault(a.name, set()).add("*")
    return {m: s for m, s in found.items()
            if not (m == self_prefix or m.startswith(self_prefix + "."))}


def _check_dependencies(rel, unit_dir, deps, rep):
    consumes = getattr(deps, "CONSUMES", {})
    declared_keys = set(consumes.keys())

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
            rep.ok(rel, f"dépendance '{src_name}': {len(symbols)} symbole(s) vérifié(s)")

    # 3a. tout import vers une unité-projet doit être déclaré dans CONSUMES
    self_prefix = ".".join(unit_dir.relative_to(REPO_ROOT).parts)
    proj = _project_imports(unit_dir, self_prefix)
    wild_mods = [m for m in proj if m not in declared_keys]
    if wild_mods:
        rep.fail(rel, f"import projet NON déclaré: {sorted(wild_mods)} (ajouter à CONSUMES)")
    elif proj:
        rep.ok(rel, f"imports projet ({len(proj)}) tous déclarés")

    # 3b. pour les sources déclarées : usage réel conforme (pas de symbole sauvage)
    used = _collect_alias_usages(unit_dir, declared_keys)
    for src_name, used_syms in used.items():
        declared = set(consumes.get(src_name, []))
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

    # Pont de migration : racine (nouveaux modules/, sql/).
    for p in (str(REPO_ROOT),):
        if p not in sys.path:
            sys.path.insert(0, p)

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
