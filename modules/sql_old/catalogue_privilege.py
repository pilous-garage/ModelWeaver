#!/usr/bin/env python3
"""catalogue_privilege — Résolution des autorisations par chemin/commande.

Table `privileges` :
  - chemin_ref : ref canonique (kind='ref'), path (kind='path') ou commande
    (kind='cmd'). UN SEUL mécanisme pour paths + commandes (fini whitelist).
  - read/write/exec/privileged : MODE UNIX 4 GROUPES (1 char par niveau) :
        position 0 = humain_with_root, 1 = humain, 2 = agent_with_root,
        3 = agent. 'r'/'w'/'x'/'p' présent, '-' absent.
  - level : 0..MAX_UINT32 ; MAX_UINT32 = TOUJOURS (priorité absolue).
  - deadline : date ISO d'expiration (NULL/'' = jamais).

Conditions (table privilege_conditions, une ligne par condition,
INTERSECTION) :
  - nb_times      : valeur = nb max ; compteur décrémenté à CHAQUE check OK.
  - until_restart : valide jusqu'au prochain redémarrage (pas de compteur).
  - until_date    : valide jusqu'à une date (valeur = ISO).
  - ref_id        : hérite de la validité de l'autorisation id_auth=valeur.

Résolution : lignes du PLUS HAUT level qui matchent (agent_id/team ciblés ou
-1), puis INTERSECTION des modes. Une ligne expirée (deadline) ou à condition
non satisfaite est écartée ; nb_times décrémente.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

LEVELS = ("humain_with_root", "humain", "agent_with_root", "agent")
LEVEL_INDEX = {name: i for i, name in enumerate(LEVELS)}

# Matrice de DÉLÉGATION : qui peut autoriser qui (hiérarchie stricte).
# Un décideur de niveau D ne peut approuver une demande que pour un
# bénéficiaire de niveau B si B ∈ DELEGATION[D]. La chaîne :
#   humain_with_root (0) délivre à agent_with_root (2), qui délivre à
#   agent (3). Un niveau ne peut jamais déléguer à un niveau PLUS PRIVILÉGIÉ.
DELEGATION: Dict[str, set] = {
    "humain_with_root": {"humain_with_root", "humain", "agent_with_root", "agent"},
    "humain": {"humain", "agent"},
    "agent_with_root": {"agent_with_root", "agent"},
    "agent": {"agent"},
}


def can_delegate(decider_level: str, beneficiary_level: str) -> bool:
    """Vrai si un décideur de niveau `decider_level` peut approuver une
    autorisation pour un bénéficiaire de niveau `beneficiary_level`."""
    return beneficiary_level in DELEGATION.get(decider_level, set())


def _utcnow() -> datetime:
    """Horodatage UTC naïf, cohérent avec datetime('now') SQLite."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

COLUMN_FLAG = {"read": "r", "write": "w", "exec": "x", "privileged": "p"}
COLUMNS = tuple(COLUMN_FLAG.keys())


class PrivilegeError(ValueError):
    pass


def validate_mode(mode: str, col: str) -> str:
    """Valide/normalise un mode 4 groupes (1 char par niveau)."""
    if mode is None or mode == "":
        return "----"
    mode = str(mode)
    if len(mode) != 4:
        raise PrivilegeError(
            f"mode '{col}' invalide: {mode!r} — attendu 4 chars "
            f"({', '.join(LEVELS)})")
    flag = COLUMN_FLAG.get(col, "?")
    for ch in mode:
        if ch not in (flag, "-"):
            raise PrivilegeError(
                f"mode '{col}' invalide: {mode!r} — chars autorisés '{flag}' ou '-'")
    return mode


def intersect_modes(modes: List[str], col: str) -> str:
    """Intersection (le plus restrictif) de plusieurs modes 4-groupes.

    Un '-' présent dans UNE ligne → '-' pour ce niveau."""
    if not modes:
        return "----"
    flag = COLUMN_FLAG.get(col, "?")
    out = []
    for i in range(4):
        out.append(flag if all(len(m) == 4 and m[i] == flag for m in modes) else "-")
    return "".join(out)


def select_by_level(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Ne garde que les lignes du PLUS HAUT level (importance)."""
    if not rows:
        return []
    max_level = max(r.get("level", 1) or 1 for r in rows)
    return [r for r in rows if (r.get("level", 1) or 1) == max_level]


def parse_privileges(row: Dict[str, Any]) -> Dict[str, str]:
    """Extrait {colonne: mode} d'une ligne, avec défauts '----'."""
    return {col: validate_mode(row.get(col, ""), col) for col in COLUMNS}


def _now() -> str:
    return _utcnow().isoformat(timespec="seconds")


def is_deadline_passed(row: Dict[str, Any]) -> bool:
    """Vrai si la deadline temporelle de la ligne est passée."""
    dl = row.get("deadline")
    if not dl:
        return False
    try:
        return datetime.fromisoformat(str(dl)) < _utcnow()
    except (TypeError, ValueError):
        return False


def conditions_satisfied(conditions: List[Dict[str, Any]],
                         ref_lookup) -> bool:
    """Toutes les conditions de l'autorisation sont-elles satisfaites ?

    ``ref_lookup`` : callable(id_auth) → row privileges (pour ref_id)."""
    for c in conditions:
        ctype = c.get("condition_type")
        val = c.get("valeur")
        if ctype == "until_restart":
            # flag de redémarrage : géré côté appelant (reset), ici toujours
            # vrai (l'état de session décide).
            continue
        if ctype == "until_date":
            try:
                if datetime.fromisoformat(str(val)) < _utcnow():
                    return False
            except (TypeError, ValueError):
                return False
        elif ctype == "ref_id":
            ref = ref_lookup(int(val)) if val else None
            if ref is None or is_deadline_passed(ref):
                return False
            # hérite aussi des conditions de la ref
            if conditions_satisfied(ref.get("_conditions") or [], ref_lookup):
                continue
            return False
        elif ctype == "nb_times":
            n = c.get("compteur")
            try:
                if n is None or int(n) <= 0:
                    return False
            except (TypeError, ValueError):
                return False
        elif ctype == "lastcall":
            # limite de cadence : au moins `valeur` secondes entre 2 usages.
            try:
                min_interval = float(val or 0)
            except (TypeError, ValueError):
                min_interval = 0.0
            last = c.get("last_use_at")
            if last:
                try:
                    last_dt = datetime.fromisoformat(str(last))
                    if (_utcnow() - last_dt).total_seconds() < min_interval:
                        return False
                except (TypeError, ValueError):
                    pass  # pas d'horodatage → autorisé (premier usage)
    return True


def consume_conditions(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Consomme les conditions d'usage (modify_use).

    - nb_times : décrémente le compteur.
    - lastcall : met à jour last_use_at (horodatage du dernier usage).
    Appelée après une vérification POSITIVE d'une autorisation (modify_use)."""
    out = []
    for c in conditions:
        if c.get("condition_type") == "nb_times":
            try:
                n = int(c.get("compteur", 0))
                c["compteur"] = n - 1
            except (TypeError, ValueError):
                pass
        elif c.get("condition_type") == "lastcall":
            c["last_use_at"] = _utcnow().isoformat(timespec="seconds")
        out.append(c)
    return out
