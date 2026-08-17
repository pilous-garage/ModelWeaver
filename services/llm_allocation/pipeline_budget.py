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


def allocate_step(cat, sub_task_id: int, step_type: str, difficulty: str,
                  budget: Dict[str, Dict[str, float]],
                  agent_id: str, adresse_runtime_id: int) -> bool:
    """Ouvre l'allocation agent→budget d'une étape (nature quota).

    L'enveloppe de l'étape (vecteur opti) devient le montant de l'allocation
    QUOTA de l'agent sur cette adresse. La dimension de référence = money
    (fallback req si money=0). Best-effort.
    """
    try:
        opti = budget.get("opti", {})
        # dimension de référence : money, sinon req (les seules dimensions
        # où l'allocation agent consomme actuellement).
        ref_value = float(opti.get("money", 0) or 0)
        ref_nature = "money"
        if ref_value <= 0:
            ref_value = float(opti.get("req", 0) or 0)
            ref_nature = "req"
        if ref_value <= 0:
            return False
        from services.llm_usage.allocation import allocate
        from modules.sql.catalogue_repo import CatalogueDB
        cat = cat or CatalogueDB()
        # budget_final de l'adresse (tag req — l'allocation quota se fait sur
        # une ligne budget_final précise).
        bf = cat.conn.execute(
            "SELECT budget_final_id FROM budget_final WHERE adresse_runtime_id = ? "
            "ORDER BY budget_final_id LIMIT 1", (adresse_runtime_id,)).fetchone()
        if not bf:
            return False
        # fraction = montant_etape / quota_effectif (si quota > 0)
        bf_row = cat.conn.execute(
            "SELECT quota_effectif FROM budget_final WHERE budget_final_id = ?",
            (bf["budget_final_id"],)).fetchone()
        quota = float(bf_row["quota_effectif"] or 0) if bf_row else 0
        fraction = (ref_value / quota) if quota > 0 else 0.0
        alloc_id = allocate(
            cat, agent_id, adresse_runtime_id, bf["budget_final_id"],
            nature="quota", fraction=min(fraction, 1.0),
            souplesse="strict")
        return alloc_id is not None
    except Exception:
        return False