"""rates — calcul des débits (RPM/TPM/RPH/TPH/RPD/TPD) à partir d'une séquence.

L'arrondi est supérieur sur les unités temporelles :
  - durée 17s     → 1 minute (unité), 1 heure, 1 jour
  - durée 1min15s → 2 minutes, 1 heure, 1 jour
On ne stocke PAS l'arrondi : get_rate() le calcule à la volée avec la durée
en secondes.

Une séquence (ligne de model_success_runs ou d'un batch usage_history_*)
contient : duration_s (si dispo), req_total, tok_total, et éventuellement
req_<type>/tok_<type> par call_type (chat, chat_stream, probe…).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

# Unités temporelles (secondes) pour les débits.
UNITS = {
    "min": 60,
    "h": 3600,
    "d": 86400,
}


def ceil_units(duration_s: Optional[int], unit_s: int) -> int:
    """Nombre d'unités temporelles arrondi au supérieur (min 1).

    duration 17s, unit 60 → 1 ; duration 75s, unit 60 → 2.
    duration None ou 0 → 1 (on ne divise jamais par zéro).
    """
    if not duration_s or duration_s <= 0:
        return 1
    return max(1, math.ceil(duration_s / unit_s))


def get_rate(sequence: Dict[str, Any], duration_limit_s: Optional[int] = None) -> Dict[str, Any]:
    """Calcule TOUS les débits d'une séquence (dict facile à décrypter).

    sequence : dict avec au moins `duration_s` (int), `req_total`, `tok_total`,
    et optionnellement `req_<type>`/`tok_<type>` par call_type.

    duration_limit_s : si fourni, borne la durée utilisée (ex. ne pas
    considérer plus de 3600 s pour un débit horaire). Sinon on utilise
    duration_s tel quel.

    Retour :
    {
      "duration_s": 75,
      "req": {"min": 17, "h": 1, "d": 1},   // requests par unité
      "tok": {"min": 340, "h": 8, "d": 1},  // tokens par unité
      "by_type": {
        "chat": {"req": {"min": 10, "h": 1, "d": 1},
                 "tok": {"min": 200, "h": 1, "d": 1}},
        ...
      }
    }
    """
    dur = sequence.get("duration_s") or 0
    if duration_limit_s and duration_limit_s > 0:
        dur = min(dur, duration_limit_s) if dur > 0 else duration_limit_s

    req_total = int(sequence.get("req_total") or sequence.get("requests") or 0)
    tok_total = int(sequence.get("tok_total")
                    or (sequence.get("tokens_in") or 0)
                    + (sequence.get("tokens_out") or 0))

    # Rates globaux (req/tok par minute/heure/jour).
    req = {}
    tok = {}
    for name, unit in UNITS.items():
        u = ceil_units(dur, unit)
        req[name] = round(req_total / u, 2) if req_total else 0
        tok[name] = round(tok_total / u, 2) if tok_total else 0

    # Rates par type d'appel (req_<type>, tok_<type> si présents).
    # On ignore req_total/tok_total (agrégats globaux, pas des types).
    by_type: Dict[str, Any] = {}
    for key, val in sequence.items():
        if key.startswith("req_") and key != "req_total":
            ctype = key[len("req_"):]
            req_t = int(val or 0)
            tok_t = int(sequence.get(f"tok_{ctype}") or 0)
            rt, tt = {}, {}
            for name, unit in UNITS.items():
                u = ceil_units(dur, unit)
                rt[name] = round(req_t / u, 2) if req_t else 0
                tt[name] = round(tok_t / u, 2) if tok_t else 0
            by_type[ctype] = {"req": rt, "tok": tt}

    return {
        "duration_s": dur,
        "req": req,
        "tok": tok,
        "by_type": by_type,
    }


def rate_fields(call_types: Optional[list] = None) -> list:
    """Colonnes req_<type>/tok_<type> pour un ensemble de call_types."""
    types = call_types or ["chat", "chat_stream", "probe"]
    cols = []
    for t in types:
        safe = t.replace(" ", "_").replace("-", "_")
        cols.append(f"req_{safe}")
        cols.append(f"tok_{safe}")
    return cols


def ensure_rate_columns(conn, table: str, call_types: Optional[list] = None) -> None:
    """Crée PAresseusement les colonnes req_<type>/tok_<type> si absentes.

    Ajoute aussi req_total/tok_total si absents. Best-effort (race possible
    entre threads → on ignore « duplicate column »).
    """
    try:
        cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return
    to_add = ["req_total", "tok_total"]
    for c in rate_fields(call_types):
        if c not in cols:
            to_add.append(c)
    for c in to_add:
        if c in cols:
            continue
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {c} INTEGER DEFAULT 0")
        except Exception:
            pass
