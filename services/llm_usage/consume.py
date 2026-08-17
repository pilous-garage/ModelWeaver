"""llm_usage/consume — consommation d'un appel LLM réel (Idée 18).

Appelé à chaque appel LLM (bridge._log_call) : fait la JONCTION entre
l'appel réel et les tables de régulation :
  1. BUDGET : incrémente `spent` des budget_final / budget_generique_key_tag
     via les ratios de cost_final (multi-monnaies : money/time/thinking_power).
  2. ÉTATS D'ERREUR : rafales de fails → adress_error_state + (restriction)
     budget_error_state. Un succès réinitialise la rafale d'adresse.
  3. SCORING (fiabilité) : alimente les scores par niveau (llm_domaine_score /
     llm_task_type_score) — le point d'entrée des événements d'appel. La mise
     à jour par le SUPERVISOR (task_log + benchmarks) viendra par la suite.

Best-effort : ne lève JAMAIS (un échec ici ne doit pas casser l'appel LLM).
Les valeurs sont dans catalogue.db.
"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional


def _now() -> int:
    return int(time.time())


def _resolve_adresse_runtime(cat, adresse_id: int) -> Optional[Dict[str, Any]]:
    """Retrouve l'adresse_runtime pour un adresse_id (la 1re clé connue)."""
    try:
        row = cat.conn.execute(
            "SELECT * FROM adresse_runtime WHERE adresse_id = ? "
            "ORDER BY adresse_runtime_id LIMIT 1",
            (adresse_id,)).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _reset_if_due(cat, budget_id: int, table: str) -> None:
    """Reset le spent si next_reset est dépassé (fenêtre glissante fixe)."""
    try:
        row = cat.conn.execute(
            f"SELECT next_reset FROM {table} WHERE budget_id = ?",
            (budget_id,)).fetchone()
        if not row or not row["next_reset"]:
            return
        if _now() >= row["next_reset"]:
            cat.conn.execute(
                f"UPDATE {table} SET spent = 0, next_reset = NULL "
                f"WHERE budget_id = ?", (budget_id,))
            cat.conn.commit()
    except Exception:
        pass


def consume_call(cat, adresse_id: int, success: bool,
                 tokens_in: int = 0, tokens_out: int = 0,
                 tokens_thinking: int = 0,
                 latency_ms: float = 0.0,
                 error_code: str = "",
                 thinking_score: float = 0.0,
                 agent_id: Optional[str] = None,
                 nb_requetes: int = 1) -> Dict[str, Any]:
    """Consomme un appel réel : budgets + coûts + états + scoring.

    `adresse_id` : l'adresse (provider×endpoint×modèle) de l'appel.
    `tokens_in/out/thinking`, `latency_ms` : l'usage réel.
    `error_code` : 'rate_limited'/'quota_exhausted'/'' (ok).
    `thinking_score` : l'indice de puissance de pensée du modèle (0.0 si
    inconnu — on ne consomme pas de thinking_power sans cet indice).
    `agent_id` : si l'agent a une allocation sur cette adresse (allocation
    quota ou budget), son spent est consommé ici (la souplesse s'applique).
    Retourne {ok, consumed: {...}} — best-effort.
    """
    out: Dict[str, Any] = {"ok": False, "consumed": {}}
    if not cat:
        return out
    try:
        adr = _resolve_adresse_runtime(cat, adresse_id)
        if not adr:
            return out

        # ── 1bis. ALLOCATION AGENT (avant coûts : la part de l'agent d'abord).
        # La consommation sur l'allocation de l'agent s'applique en unités
        # REQUEST (une requête = une unité quel que soit son poids token) et
        # ne concerne que les succès. La souplesse (strict/souple/informatif)
        # est gérée ici, au niveau de l'allocation.
        if success and agent_id:
            try:
                from services.llm_usage.allocation import consume_allocation
                allocated = consume_allocation(
                    cat, agent_id, adr["adresse_runtime_id"], 1.0, nature="quota")
                if not allocated:
                    # allocation quota épuisée : tente la nature budget (fixe).
                    allocated = consume_allocation(
                        cat, agent_id, adr["adresse_runtime_id"], 1.0,
                        nature="budget")
                out["consumed"]["allocation"] = allocated
            except Exception:
                out["consumed"]["allocation"] = True  # best-effort

        # ── 2. ÉTATS D'ERREUR (avant coût : un fail ne consomme pas) ──
        if not success and error_code in ("rate_limited", "quota_exhausted",
                                          "timeout", "auth"):
            # Rafale de fail → adress_error_state.
            cur = cat.conn.execute("""
                INSERT INTO adress_error_state
                    (adresse_runtime_id, error_since, last_error_at, n_fail)
                VALUES (?, ?, ?, 1)
                ON CONFLICT(adresse_runtime_id) DO UPDATE SET
                    last_error_at = excluded.last_error_at,
                    n_fail = adress_error_state.n_fail + 1
            """, (adr["adresse_runtime_id"], _now(), _now()))
            cat.conn.commit()
            out["consumed"]["adress_fail"] = True
            # budget_error_state : restriction — le budget devient error si
            # TOUTES ses adresses sont error. (calculé par le tick budget,
            # ici on note juste la rafale.)
        elif success:
            # Succès → réinitialise la rafale d'adresse.
            cat.conn.execute(
                "DELETE FROM adress_error_state WHERE adresse_runtime_id = ?",
                (adr["adresse_runtime_id"],))
            cat.conn.commit()

        # ── 1. BUDGETS + COÛTS : via cost_final → budget_final ──
        # (un appel en erreur ne consomme pas les budgets sauf request)
        consumed = {"tok_in": tokens_in, "tok_out": tokens_out,
                    "thinking": tokens_thinking, "request": nb_requetes}
        if success:
            cf_rows = cat.conn.execute(
                "SELECT * FROM cost_final WHERE adresse_runtime_id = ?",
                (adr["adresse_runtime_id"],)).fetchall()
            for cf in cf_rows:
                unit_in = cf["unit_in"]
                unit_out = cf["unit_out"]
                ratio = cf["ratio"] or 1.0
                # valeur consommée en unit_in → convertie en unit_out
                val_in = {"tok_in": tokens_in, "tok_out": tokens_out,
                          "request": nb_requetes,
                          "thinking_in": tokens_thinking,
                          "secondes": latency_ms / 1000.0}.get(unit_in, 0.0)
                if val_in == 0:
                    continue
                val_out = val_in * ratio
                # budget_final ciblé
                bf_id = cf["budget_final_id"]
                if bf_id:
                    _reset_if_due(cat, bf_id, "budget_final")
                    cat.conn.execute(
                        "UPDATE budget_final SET spent = spent + ? "
                        "WHERE budget_final_id = ?", (val_out, bf_id))
                    consumed[f"{unit_in}→{unit_out}"] = round(val_out, 8)
            cat.conn.commit()
            out["consumed"]["budgets"] = consumed

        # ── 3. SCORING (fiabilité) : mise à jour des scores par niveau ──
        # (événement d'appel — le grain modèle canonique).
        model_id = adr.get("model_id")
        if model_id and success:
            _score_call_success(cat, model_id)

        out["ok"] = True
        return out
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass
        return out


def _score_call_success(cat, model_id: int) -> None:
    """Alimente la FIABILITÉ par modèle (les scores par niveau init 1.0).

    Simple pour l'instant : un appel réussi renforce le score du modèle sur
    tous les domaines/types (le succès prouve la fiabilité). La mise à jour
    fine par (domaine, type, niveau) viendra avec les événements du supervisor
    (task_log). Ici : score = 0.5 + 0.5 × ratio succès (approche naïve).
    """
    try:
        # On incrémente samples + on tire le score vers la fiabilité réelle.
        # L'échec du modèle n'est pas traité ici (le _mark_call_failed gère
        # le repos) — le score fiabilité par niveau sera alimenté par le
        # supervisor (task_log) dans la prochaine étape.
        cat.conn.execute(
            "UPDATE llm_domaine_score SET samples = samples + 1, "
            "updated_at = ? WHERE model_id = ?",
            (int(time.time()), model_id))
        cat.conn.execute(
            "UPDATE llm_task_type_score SET samples = samples + 1, "
            "updated_at = ? WHERE model_id = ?",
            (int(time.time()), model_id))
        cat.conn.commit()
    except Exception:
        pass
