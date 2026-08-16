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
    """Alloue un LLM (sans envoyer de prompt) — ask_llm@v1."""
    return _allocate(inputs, home)


def _allocate(inputs: dict, home: str) -> dict:
    """Alloue un LLM via LLMManager.assign_llm (logique partagée ask_llm /
    ask_llm_with_prompt). Retourne {ok, provider_ref, model_ref, use_case,
    used_models} (+ not_same = liste des modèles exclus)."""
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
            "use_case": use_case, "used_models": used_models,
            "not_same": not_same}


def exec_with_prompt(inputs: dict, home: str) -> dict:
    """ask_llm_with_prompt : alloue un LLM (args classiques de ask_llm :
    use_case, not_same_modele, exclude_models, restrict_llm...) PUIS envoie la
    prompt au bridge et retourne la réponse.

    Si l'appel échoue (BridgeError : auth/quota/timeout/429), on DEMANDE UN
    AUTRE MODÈLE (exclusion du défaillant via not_same_modele) et on retente —
    jusqu'à `max_essais` (défaut 3). Retourne {ok, provider_ref, model_ref,
    used_models, response, fallbacks}.

    C'est le pont allocation → génération en UN SEUL skill : l'agent ne doit
    pas gérer lui-même le couple alloc puis chat + retry.
    """
    prompt = (inputs.get("prompt") or "").strip()
    if not prompt:
        return {"ok": False, "error": "prompt requis"}
    system = inputs.get("system") or ""
    temperature = float(inputs.get("temperature", 0.7) or 0.7)
    max_tokens = int(inputs.get("max_tokens", 4096) or 4096)
    timeout = int(inputs.get("timeout", 90) or 90)
    max_essais = int(inputs.get("max_essais", 3) or 3)
    agent_id = inputs.get("agent_id", "") or _agent_id_from_home(home)
    # used_models : modèles déjà tentés/exclus → on en redemande un autre à
    # chaque échec.
    used = list(inputs.get("not_same_modele") or [])
    if isinstance(used, str):
        used = [m.strip() for m in used.split(",") if m.strip()]
    used = [m for m in used if m]

    last_err = ""
    for essai in range(max_essais):
        alloc = _allocate(dict(inputs, not_same_modele=used), home)
        if not alloc.get("ok"):
            last_err = alloc.get("error", "allocation échouée")
            break
        p_ref = alloc["provider_ref"]
        m_ref = alloc["model_ref"]
        if m_ref not in used:
            used = list(used) + [m_ref]
        msgs = ([{"role": "system", "content": system}] if system else []) + \
               [{"role": "user", "content": prompt}]
        try:
            from modules.llm_manager.resilient import resilient_chat
            resp = resilient_chat(
                p_ref, m_ref, msgs, timeout=timeout, fallback=True,
                use_case=alloc["use_case"], agent_id=agent_id or None,
                temperature=temperature, max_tokens=max_tokens)
            content = (getattr(resp, "content", "") or "").strip()
            return {"ok": True, "provider_ref": p_ref, "model_ref": m_ref,
                    "response": content, "used_models": used,
                    "fallbacks": getattr(resp, "fallbacks", 0),
                    "use_case": alloc["use_case"]}
        except Exception as e:
            last_err = str(e)
            # Échec → demander un AUTRE modèle (le défaillant est exclu).
            if m_ref not in used:
                used = list(used) + [m_ref]
    return {"ok": False, "provider_ref": "", "model_ref": "",
            "response": "", "used_models": used,
            "error": f"échec après {max_essais} essais: {last_err}"}


__skills__ = ["exec", "exec_with_prompt"]
