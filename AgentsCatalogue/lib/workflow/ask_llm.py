"""ask_llm — alloue un LLM à l'agent via LLMManager.assign_llm.

use_case="coding" exige function_calling (donc un modèle agentic). Le FSM
capture provider_ref/model_ref dans ses variables (_llm_provider/_llm_model)
et les réutilise pour les steps llm_call suivants.
"""

import re


def _agent_id_from_home(home: str) -> str:
    m = re.search(r"agent_home/(\d+)", home or "")
    return m.group(1) if m else ""


def _resolve_use_case(use_case: str, min_score: float) -> str:
    """coding_high = coding avec un seuil de score élevé."""
    if use_case in ("coding_high",):
        return "coding"
    return use_case or "coding"


def exec(inputs: dict, home: str) -> dict:
    from modules.llm_manager.llm_manager import LLMManager
    from modules.sql.catalogue_repo import CatalogueDB

    use_case = _resolve_use_case(inputs.get("use_case", "coding"),
                                 float(inputs.get("min_score", 0) or 0))
    agent_id = inputs.get("agent_id", "") or _agent_id_from_home(home)
    min_window = int(inputs.get("min_window", 0) or 0)
    min_score = float(inputs.get("min_score", 0) or 0)
    # restrict_llm : allowlist de modèles → exclusions (tout le reste).
    # exclusions explicites (exclude_models) ajoutées.
    exclude_models = list(inputs.get("exclude_models") or [])
    exclude_providers = list(inputs.get("exclude_providers") or [])
    # not_same_modele : modèles DÉJÀ alloués à d'autres agents (consensus) →
    # on les exclut pour obtenir un modèle DIFFÉRENT. Chaîne CSV acceptée.
    not_same = inputs.get("not_same_modele") or []
    if isinstance(not_same, str):
        not_same = [m.strip() for m in not_same.split(",") if m.strip()]
    not_same = [m for m in not_same if m]
    exclude_models = list(set(exclude_models) | set(not_same))
    allow = inputs.get("restrict_llm") or []
    if isinstance(allow, str):
        allow = [m.strip() for m in allow.split(",") if m.strip()]
    # Si non fourni dans inputs, relire depuis les variables de l'agent BDD
    # (injectées par dev-chat / swarm-as-llm via _run_pilot).
    if not exclude_models and not allow:
        try:
            from modules.sql.agents_repo import AgentsDB
            import json as _json
            _aid = _agent_id_from_home(home)
            if _aid:
                db = AgentsDB()
                row = db.conn.execute(
                    "SELECT variables_json FROM agents WHERE agent_id = ?",
                    (int(_aid),)).fetchone()
                db.close()
                if row:
                    _vars = _json.loads(row["variables_json"] or "{}")
                    exclude_models = list(_vars.get("exclude_models") or [])
                    allow = _vars.get("restrict_llm") or []
                    if isinstance(allow, str):
                        allow = [m.strip() for m in allow.split(",") if m.strip()]
        except Exception:
            pass
    if allow:
        try:
            from modules.sql.catalogue_repo import CatalogueDB
            cat = CatalogueDB()
            # refs complètes (catalogue_models.ref, ex. nvidia/meta/llama-…):
            # la colonne provider_models.ref n'existe pas (seulement
            # provider_model_name) → l'ancienne requête `SELECT ref` levait
            # une exception silencieuse → restrict_llm ignoré → allocation
            # d'un modèle non fiable (mimo/longcat/… rate-limités).
            all_models = [m["ref"] for m in cat.conn.execute(
                "SELECT DISTINCT m.ref "
                "FROM provider_models pm "
                "JOIN catalogue_models m ON m.id = pm.model_id").fetchall()]
            allow_norm = [m.split("/", 1)[-1] for m in allow]
            allowed = set()
            for am in all_models:
                if am in allow or am.split("/", 1)[-1] in allow_norm:
                    allowed.add(am)
            exclude_models = list(set(exclude_models) |
                                  (set(all_models) - allowed))
            # Exclure TOUS les providers hors allowlist (l'allocation peut
            # sinon choisir un modèle du même nom sur un autre provider).
            allowed_providers = set()
            for am in allowed:
                ap = am.split("/", 1)[0]
                if ap:
                    allowed_providers.add(ap)
            if allowed_providers:
                from modules.sql.catalogue_repo import CatalogueDB as _C
                _cat = _C()
                all_prov = [r["ref"] for r in _cat.conn.execute(
                    "SELECT ref FROM catalogue_providers").fetchall()]
                _cat.close()
                exclude_providers = list(
                    set(exclude_providers) |
                    (set(all_prov) - allowed_providers))
        except Exception:
            pass

    try:
        llm_mgr = LLMManager(CatalogueDB())
        llm = llm_mgr.assign_llm(use_case=use_case, agent_id=agent_id or None,
                                 min_window=min_window,
                                 exclude_models=exclude_models or None,
                                 exclude_providers=exclude_providers or None)
    except Exception as e:
        return {"ok": False, "provider_ref": "", "model_ref": "",
                "use_case": use_case, "error": f"assign_llm: {e}"}
    if not llm:
        return {"ok": False, "provider_ref": "", "model_ref": "",
                "use_case": use_case,
                "error": f"aucun modèle disponible pour use_case={use_case}"}
    p_ref = llm.get("provider_ref", "")
    m_ref = llm.get("model_ref", "")
    if not p_ref or not m_ref:
        return {"ok": False, "provider_ref": p_ref, "model_ref": m_ref,
                "use_case": use_case,
                "error": "assign_llm a retourné provider/model vides"}
    # used_models : liste à jour des modèles occupés (pour le prochain appel
    # consensus — not_same_modele du suivant = used_models du précédent).
    used_models = list(not_same) + [m_ref]
    return {"ok": True, "provider_ref": p_ref, "model_ref": m_ref,
            "use_case": use_case, "used_models": used_models}


__skills__ = ["exec"]
