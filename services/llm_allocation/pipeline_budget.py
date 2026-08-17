"""llm_allocation/pipeline_budget — BUDGET PAR PIPELINE (Idée 18, O4).

À la DÉCOUPE d'une tâche, on estime le budget global du pipeline (enveloppe)
puis on le répartit par ÉTAPE selon la stratégie de la team. Chaque étape
porte un VECTEUR 6D opti/max : {money, time, thinking_power, req, tok_in,
tok_out} — budget_opti (la part théorique) et budget_max (opti × multiplicateur,
au-delà ON S'ARRÊTE).

À l'attribution d'une sub_task à un agent, l'enveloppe de l'étape OUVRE une
allocation agent→budget (nature quota) — vérifiée par consume_call à chaque
appel.

Répartition par défaut (budgets de RÉFÉRENCE, surtout pour les gratuits) :
  planning 10% · coding 40% · reviewing 20% · testing 20% · merging 10%.
Surchargeable par la team (manifest: allocation_strategy).

Best-effort : ne lève jamais.
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Tuple

# Répartition par défaut du budget du pipeline (surchargeable par la team).
DEFAULT_STRATEGY: Dict[str, float] = {
    "planning": 0.10,
    "coding": 0.40,
    "reviewing": 0.20,
    "testing": 0.20,
    "merging": 0.10,
}

# Multiplicateur budget_max par rapport à budget_opti (on s'arrête au-delà).
OPT_MAX_MULT = 1.5

# Les 6 dimensions du vecteur budget d'une étape.
DIMS = ("money", "time", "thinking_power", "req", "tok_in", "tok_out")


def _now() -> int:
    return int(time.time())


def normalize_strategy(strategy: Optional[Dict[str, float]]) -> Dict[str, float]:
    """Normalise une stratégie : poids par type, somme → 1.0."""
    s = dict(strategy or {})
    if not s:
        s = dict(DEFAULT_STRATEGY)
    total = sum(float(v) for v in s.values() if v is not None and v > 0)
    if total <= 0:
        return dict(DEFAULT_STRATEGY)
    return {k: float(v) / total for k, v in s.items() if v is not None and v > 0}


def estimate_enveloppe(cat, task_type: str, difficulty: str,
                       nb_steps: int) -> Dict[str, float]:
    """Enveloppe GLOBALE du pipeline (vecteur 6D) pour une découpe.

    Estimation à partir du calculateur (llm_task_cost) si disponible, sinon
    heuristique par type/niveau. `nb_steps` = nb de sub_tasks créées.
    Retourne {money, time, thinking_power, req, tok_in, tok_out}.
    """
    out = {d: 0.0 for d in DIMS}
    try:
        # Coût estimé par (type, niveau) via le calculateur (moyenne sur les
        # modèles connus) — enveloppe = Σ étapes.
        niveau = {
            "easy": "debutant", "medium": "junior",
            "hard": "intermediaire", "expert": "senior",
        }.get((difficulty or "medium").lower(), "junior")
        tt = cat.conn.execute(
            "SELECT id FROM scoring_task_types WHERE code = ?",
            (task_type,)).fetchone()
        if tt and nb_steps > 0:
            rows = cat.conn.execute("""
                SELECT AVG(tok_in) a_in, AVG(tok_out) a_out, AVG(tok_think) a_th,
                       AVG(req) a_req, AVG(temps) a_tm
                FROM llm_task_cost WHERE task_type_id = ? AND niveau = ?
            """, (tt["id"], niveau)).fetchall()
            r = rows[0] if rows else None
            if r and (r["a_in"] or 0) > 0:
                out["tok_in"] = round(float(r["a_in"] or 0) * nb_steps, 4)
                out["tok_out"] = round(float(r["a_out"] or 0) * nb_steps, 4)
                out["tok_think"] = round(float(r["a_th"] or 0) * nb_steps, 4)
                out["req"] = round(float(r["a_req"] or 0) * nb_steps, 4)
                out["time"] = round(float(r["a_tm"] or 0) * nb_steps, 4)
                # money/thinking_power : dérivés simplifiés (non renseignés
                # dans llm_task_cost pour l'instant) → 0 par défaut.
                out["money"] = 0.0
                out["thinking_power"] = 0.0
    except Exception:
        pass
    return out


def split_enveloppe(enveloppe: Dict[str, float],
                    strategy: Dict[str, float],
                    step_types: List[str]) -> Dict[str, Dict[str, float]]:
    """Répartit l'enveloppe globale par étape (vecteur 6D opti par type).

    `step_types` : les types des sub_tasks créées (ex. ['coding','coding',
    'testing','review']). Chaque type reçoit sa part de l'enveloppe selon la
    stratégie normalisée. Retourne {type: {dim: part_opti}}.
    """
    norm = normalize_strategy(strategy)
    # nb d'étapes par type (pour diviser la part du type entre ses instances)
    counts: Dict[str, int] = {}
    for t in step_types:
        counts[t] = counts.get(t, 0) + 1
    total_types = sum(counts.values()) or 1
    out: Dict[str, Dict[str, float]] = {}
    for t, n in counts.items():
        # part du type = poids_normalisé (le poids déjà normalisé sur la
        # somme des types → si un type absent de la stratégie, poids 0).
        w = norm.get(t, 0.0)
        part = {d: round(float(enveloppe.get(d, 0)) * w, 4) for d in DIMS}
        # diviser entre les instances du type.
        part = {d: round(v / n, 4) if n else v for d, v in part.items()}
        out[t] = part
    return out


def step_budget(part: Dict[str, float]) -> Dict[str, Dict[str, float]]:
    """Vecteur opti/max d'une étape à partir de sa part."""
    opti = {d: float(part.get(d, 0.0)) for d in DIMS}
    maxi = {d: round(v * OPT_MAX_MULT, 4) for d, v in opti.items()}
    return {"opti": opti, "max": maxi}


def open_agent_allocation_for_task(ws, cat, agent_id: str,
                                   sub_task_id: int) -> Dict[str, Any]:
    """Ouvre l'allocation agent→budget pour une sub_task attribuée.

    Appelé à l'attribution (supervisor.assign → _answer). L'enveloppe
    théorique de la sub_task (task_budget_tracking.theo_req) devient le
    montant de l'allocation QUOTA de l'agent sur l'adresse de son llm_pref
    (provider/model privilégiés) si disponible.

    Comme le modèle exact n'est choisi qu'à l'appel (assign_llm), l'allocation
    porte sur l'adresse PRIVILÉGIÉE ; si l'agent n'en a pas, on signale qu'il
    n'y a pas d'allocation dédiée (le consume_call gérera par l'adresse appelée).
    Retourne {ok, allocation_id?, reason}.
    """
    try:
        from services.llm_usage.task_track import _workspace_for
        if cat is None:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
        # budget théorique de la sub_task (theo_req = la dimension de référence)
        db, ws_id = _workspace_for(sub_task_id, None)
        if db is None:
            return {"ok": False, "reason": "sub_task introuvable"}
        try:
            theo = db.conn.execute(
                "SELECT theo_req, model_id, assigned_to FROM task_budget_tracking "
                "WHERE sub_task_id = ?", (sub_task_id,)).fetchone()
        except Exception:
            theo = None
        if not theo:
            db.close()
            return {"ok": False, "reason": "pas de suivi budgétaire pour cette sub_task"}
        theo_req = float(theo["theo_req"] or 0)
        if theo_req <= 0:
            db.close()
            return {"ok": False, "reason": "theo_req nul (budget non estimé)"}
        # adresse privilégiée de l'agent (llm_pref)
        adresse_runtime_id = None
        try:
            from modules.sql.sql_module import AgentsDB
            adb = AgentsDB()
            ar = adb.conn.execute(
                "SELECT resources_json FROM agents WHERE name = ?",
                (agent_id,)).fetchone()
            pref = {}
            if ar:
                import json as _json
                res = _json.loads(ar["resources_json"] or "{}")
                pref = res.get("llm_pref", {})
            provider = pref.get("provider", "")
            model = pref.get("model", "")
            if provider and model:
                from services.llm_allocation.address import resolve_address
                adr = resolve_address(provider, model, cat)
                if adr:
                    row = cat.conn.execute(
                        "SELECT adresse_runtime_id FROM adresse_runtime "
                        "WHERE adresse_id = ? LIMIT 1", (adr,)).fetchone()
                    adresse_runtime_id = row["adresse_runtime_id"] if row else None
        except Exception:
            adresse_runtime_id = None
        db.close()
        if not adresse_runtime_id:
            return {"ok": False,
                    "reason": "pas de llm_pref résolvable — allocation à l'appel"}
        # budget_final de l'adresse (tag req) + allocation quota = theo_req.
        bf = cat.conn.execute(
            "SELECT budget_final_id, quota_effectif FROM budget_final "
            "WHERE adresse_runtime_id = ? AND tag_id = 2 LIMIT 1",
            (adresse_runtime_id,)).fetchone()
        if not bf:
            return {"ok": False, "reason": "pas de budget_final (tag req)"}
        quota = float(bf["quota_effectif"] or 0)
        fraction = min(theo_req / quota, 1.0) if quota > 0 else 0.0
        from services.llm_usage.allocation import allocate
        alloc_id = allocate(
            cat, agent_id, adresse_runtime_id, bf["budget_final_id"],
            nature="quota", fraction=fraction, souplesse="strict")
        return {"ok": alloc_id is not None, "allocation_id": alloc_id,
                "adresse_runtime_id": adresse_runtime_id,
                "theo_req": theo_req}
    except Exception:
        return {"ok": False, "reason": "erreur best-effort"}


def open_pipeline_tracking(ws, cat, workspace_id: str, task_id: int,
                           steps: List[tuple],
                           strategy: Optional[Dict[str, float]] = None,
                           task_difficulty: str = "medium") -> Dict[str, Any]:
    """Ouvre le SUIVI BUDGÉTAIRE des sub_tasks créées par une découpe.

    `steps` : liste de (sub_task_id, sub_task_type) créées par la découpe.
    On estime l'enveloppe globale (estimate_enveloppe), on la répartit par
    type (split_enveloppe) et on ouvre le suivi théorique de CHAQUE sub_task
    avec sa part (theo_override).

    Retourne {ok, enveloppe, par_type, tracked}.
    """
    try:
        from services.llm_usage.task_track import open_tracking
        step_types = [t for _, t in steps]
        if not step_types:
            return {"ok": False, "reason": "aucune étape"}
        main_type = step_types[0]
        enveloppe = estimate_enveloppe(cat, main_type, task_difficulty,
                                       nb_steps=len(step_types))
        parts = split_enveloppe(enveloppe, strategy or {}, step_types)
        tracked = []
        for sub_task_id, stype in steps:
            part = parts.get(stype, {})
            # le suivi théorique d'UNE instance = sa part du type
            theo = {d: part.get(d, 0.0) for d in DIMS}
            tid = open_tracking(
                ws, cat, workspace_id, sub_task_id, task_id, stype,
                task_difficulty, theo_override=theo)
            if tid:
                tracked.append({"sub_task_id": sub_task_id, "type": stype,
                                "tracking_id": tid, "theo": theo})
        return {"ok": True, "enveloppe": enveloppe, "par_type": parts,
                "tracked": tracked}
    except Exception:
        return {"ok": False}