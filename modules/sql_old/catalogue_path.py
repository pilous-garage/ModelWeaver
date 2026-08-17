#!/usr/bin/env python3
"""catalogue_path — Résolveur d'environnement de paths local.

Sémantique (verrouillée) :
  - `ref` d'une entrée = ADRESSE SYSTÈME absolue (chemin disque réel).
  - `path` d'une entrée = SYMBOLIQUE, variable, configurable (déclaré ici).
  - Résolution par PRÉCISION, de gauche à droite : le path dont le premier
    segment LITTÉRAL est le plus à gauche gagne. Ex. pour /lib/a/b :
      /lib/a/$1  (x) prime sur /lib/$1/b (y)  → résultat x.
  - Variables nommées $1, $2… substituées à la résolution (dans path_name
    ET dans address).
  - Interdictions : path_name ne commence JAMAIS par /$ ou $ ; les écritures
    de chemin interdisent `*` (les lectures seules peuvent globber).
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

_WILDCARD_RE = re.compile(r"\*")


class PathSyntaxError(ValueError):
    pass


class PathResolutionError(KeyError):
    pass


def validate_path_name(path_name: str) -> None:
    """Valide une déclaration de path (writer)."""
    if not path_name or not path_name.startswith("/"):
        raise PathSyntaxError(f"path_name doit commencer par '/': {path_name!r}")
    stripped = path_name.lstrip("/")
    if stripped.startswith("$") or stripped.startswith("/$"):
        raise PathSyntaxError(
            f"path_name ne peut pas commencer par /$ ou $: {path_name!r}")
    if _WILDCARD_RE.search(path_name):
        raise PathSyntaxError(
            f"`*` interdit dans les déclarations de path (écriture): {path_name!r}")


def validate_address(address: str) -> None:
    if not address or not address.startswith("/"):
        raise PathSyntaxError(f"address doit être absolue (commence par '/'): {address!r}")


def _substitute(template: str, vars_map: Dict[str, str]) -> str:
    """Substitue $1, $2… dans un template."""
    if not template or not vars_map:
        return template

    def _repl(m):
        return vars_map.get(m.group(0), m.group(0))

    return re.sub(r"\$\d+", _repl, template)


def resolve_path(paths: List[Dict[str, Any]], target: str) -> Tuple[str, str]:
    """Résout `target` (un path symbolique ou une adresse) parmi les paths.

    ``paths`` : liste de déclarations {path_name, address, scheme}.
    Retourne (adresse_résolue, scheme). Lève PathResolutionError si aucun
    path ne matche.

    PRÉCISION gauche→droite : score = (nb_variables, position du PREMIER
    segment littéral résolu avant la 1ère variable). Le path qui matche avec
    le plus de littéraux TÔT gagne."""
    tgt = target.strip("/").split("/")
    best: Optional[Tuple[int, int, str, str]] = None  # (vars_count, first_var_pos, addr, scheme)

    for p in paths:
        pname = (p.get("path_name") or "").strip()
        addr = p.get("address") or ""
        scheme = p.get("scheme") or "file"
        if not pname:
            continue
        pat = pname.strip("/").split("/")
        if len(pat) != len(tgt):
            continue
        vars_map: Dict[str, str] = {}
        ok = True
        first_var: Optional[int] = None
        for i, (pseg, tseg) in enumerate(zip(pat, tgt)):
            if pseg.startswith("$"):
                if first_var is None:
                    first_var = i
                vars_map[pseg] = tseg
            elif pseg != tseg:
                ok = False
                break
        if not ok:
            continue
        # score de précision : peu de variables + 1ère variable la plus
        # À DROITE (plus de littéraux résolus avant) = plus précis.
        first_var_pos = first_var if first_var is not None else len(tgt)
        score = (len(vars_map), -first_var_pos)
        if best is None or score < best[0]:
            best = (score, first_var_pos, addr, scheme, vars_map)

    if best is None:
        # cible déjà une adresse absolue existante ? → utilisable telle quelle
        if target.startswith("/") and not any(
                seg.startswith("$") for seg in tgt):
            return target, "file"
        raise PathResolutionError(f"aucun path ne résout : {target}")

    addr = best[2]          # adresse système (template)
    scheme = best[3]        # schéma (file, http…)
    resolved = _substitute(addr, best[4])
    return resolved, scheme

def match_glob(paths: List[Dict[str, Any]], pattern: str) -> List[str]:
    """(lecture) Résout un glob (agent/*/home) → listes d'adresses.

    Les `*` sont autorisés ici (lecture), jamais dans les écritures."""
    tgt = pattern.strip("/").split("/")
    results = []
    for p in paths:
        pname = (p.get("path_name") or "").strip()
        addr = p.get("address") or ""
        scheme = p.get("scheme") or "file"
        if not pname:
            continue
        pat = pname.strip("/").split("/")
        if len(pat) != len(tgt):
            continue
        vars_map: Dict[str, str] = {}
        ok = True
        for pseg, tseg in zip(pat, tgt):
            if pseg.startswith("$"):
                vars_map[pseg] = tseg
            elif pseg == "*" or tseg == "*":
                continue
            elif pseg != tseg:
                ok = False
                break
        if ok:
            results.append(_substitute(addr, vars_map))
    return results
