"""capacites — gestion modulaire des capacités agentic d'un LLM (bridge).

Décide comment appeler un LLM avec des tools selon ses capacités observées :
  - native      : tool_calls API (OpenAI function calling) — confiance agentic ≥ 0.7
  - translation : tools passés dans le prompt au format ###tool_call:nom|JSON###
                  (modèle NON-agentic mais capable de suivre une convention) —
                  confiance agentic ≤ 0.3 ET agentic-translation ≥ 0.7
  - give_both   : tools API ET bloc texte simultanés — le LLM choisit son format
                  préféré (certains modèles sont meilleurs en traduction qu'en
                  tool_call natif) — confiance inconnue/faible ou les deux
  - text        : pas de tools (simple génération)

Fonctions modulaires (réutilisables par tous les entrypoints) :
  - check_capacite(bridge, cat, provider_ref, model_ref) → {mode, confidence, ...}
  - transforme_agentic_send(mode, tools, prompt) → {tools, extra_prompt, messages_extra}
  - transforme_agentic_response(mode, response, text) → {tool_calls, translated, ok}

Observation : à chaque llm_call, on écrit dans une table LÉGÈRE `capacite_log`
(log_id, provider/model/endpoint, capability, ok). La table d'expérience
model_endpoint_provider_capacite est mise à jour PAR BATCH (fonction
flush_capacite_log) au lieu d'écrire à chaque appel.
"""

import json
import re

# Formats de réponse traduite (tools passés dans le prompt).
_TRANSLATION_RE = re.compile(r"###tool_call:([A-Za-z0-9_]+)\|(.*?)###",
                             re.DOTALL)


# ── lecture / décision ──────────────────────────────────────────────────

def _capability_confidence(cat, model_ref: str, provider_ref: str,
                           capability: str):
    """Confidence (0..1) d'une capacité d'un (provider, modèle) dans la table
    d'expérience. None si inconnu."""
    try:
        row = cat.conn.execute("""
            SELECT mepc.confidence
            FROM model_endpoint_provider_capacite mepc
            JOIN catalogue_models cm ON cm.id = mepc.model_id
            WHERE (cm.ref = ? OR cm.model_key = ?)
              AND mepc.provider_id = (
                    SELECT id FROM catalogue_providers WHERE ref = ?)
              AND mepc.capability = ?
            ORDER BY mepc.updated_at DESC LIMIT 1
        """, (model_ref, model_ref, provider_ref, capability)).fetchone()
        return float(row["confidence"]) if row else None
    except Exception:
        return None


def check_capacite(bridge, cat, provider_ref: str, model_ref: str) -> dict:
    """Décide du MODE d'appel agentic pour un (provider, modèle).

    - confiance `agentic` ≥ 0.7 → mode `native` (tools API).
    - confiance `agentic` ≤ 0.3 ET `agentic-translation` ≥ 0.7 → `translation`.
    - sinon → `give_both` (on fournit tools API + bloc texte ; le LLM choisit).
    - pas de capacité connue → `give_both` (on tente, on observe).
    Retourne {mode, confidence_agentic, confidence_translation, capability}.
    """
    conf_a = _capability_confidence(cat, model_ref, provider_ref, "agentic")
    conf_t = _capability_confidence(cat, model_ref, provider_ref,
                                    "agentic-translation")
    if conf_a is not None and conf_a >= 0.7:
        mode = "native"
    elif conf_a is not None and conf_a <= 0.3 and conf_t is not None \
            and conf_t >= 0.7:
        mode = "translation"
    else:
        # Inconnu (0.5) ou les deux possibles → on fournit les deux formats.
        mode = "give_both"
    return {"mode": mode, "confidence_agentic": conf_a,
            "confidence_translation": conf_t, "capability": "agentic"}


# ── send : préparer la requête ──────────────────────────────────────────

def _translation_prompt(tools: list, allow_batch: bool = True) -> str:
    """Bloc d'instruction pour la traduction (< ~1k tokens).

    Listes les tools + le format ###tool_call:nom|JSON###. `allow_batch` :
    plusieurs tool_call peuvent être émis dans UNE SEULE réponse (batch)."""
    lines = [
        "## OUTILS DISPONIBLES",
        "Pour exécuter un outil, écris UNE ligne au format :",
        "###tool_call:nom_outil|{\"param\": \"valeur\"}###",
        "Exemple : ###tool_call:file_write_file_v1|{\"path\": \"a.py\", "
        "\"content\": \"print(1)\"}###",
    ]
    if allow_batch:
        lines.append("Tu peux émettre PLUSIEURS tool_call dans une seule "
                     "réponse (un par ligne).")
    lines += ["Après exécution des outils, donne ta réponse finale en texte.",
              "", "Outils disponibles :"]
    for t in tools:
        fn = t.get("function", {})
        name = fn.get("name", "?")
        desc = (fn.get("description", "") or "").split("\n")[0][:120]
        params = fn.get("parameters", {}).get("properties", {})
        param_names = ", ".join(list(params.keys())[:6])
        lines.append(f"- {name} : {desc}  (params: {param_names})")
    return "\n".join(lines)[:1200]


def transforme_agentic_send(mode: str, tools: list, prompt: str):
    """Prépare la requête LLM selon le mode.

    Retourne {tools, extra_prompt, translation}.
      - mode native     : tools API fournis, pas de bloc texte.
      - mode translation: PAS de tools API, bloc texte ajouté au prompt.
      - mode give_both  : tools API ET bloc texte (le LLM choisit).
      - mode text       : pas de tools.
    """
    mode = str(mode or "give_both").lower()
    if mode == "text":
        return {"tools": [], "extra_prompt": "", "translation": False}
    if mode == "native":
        return {"tools": tools or [], "extra_prompt": "", "translation": False}
    if mode == "translation":
        return {"tools": [], "extra_prompt": _translation_prompt(tools or []),
                "translation": True}
    # give_both
    return {"tools": tools or [], "extra_prompt": _translation_prompt(tools or []),
            "translation": True}


# ── response : parser + observer ────────────────────────────────────────

def transforme_agentic_response(mode: str, response, text: str = ""):
    """Parse la réponse LLM selon le mode.

    - mode translation/give_both : cherche les ###tool_call:nom|JSON### dans le
      texte (batch possible) ET les tool_calls natifs si tools fournis.
    Retourne {tool_calls, translated, ok}.
      tool_calls : liste {name, arguments} (natifs ou traduits)
      translated : True si au moins un tool est venu de la traduction
      ok         : True si au moins un tool a été produit
    """
    mode = str(mode or "give_both").lower()
    native_calls = []
    if hasattr(response, "tool_calls") and response.tool_calls:
        for tc in response.tool_calls:
            fn = tc.get("function", {})
            native_calls.append({"name": fn.get("name", ""),
                                 "arguments": fn.get("arguments", "{}"),
                                 "native": True})
    translated = []
    if mode in ("translation", "give_both"):
        body = text or (getattr(response, "content", "") or "")
        for m in _TRANSLATION_RE.finditer(body):
            name = m.group(1)
            raw = m.group(2).strip()
            try:
                args = json.loads(raw)
            except Exception:
                args = {"content": raw}
            if not isinstance(args, dict):
                args = {"content": str(args)}
            translated.append({"name": name, "arguments": args,
                               "native": False})
    all_calls = native_calls + translated
    return {"tool_calls": all_calls,
            "translated": bool(translated),
            "ok": bool(all_calls)}


# ── observation : table de log légère + maj par batch ───────────────────

def log_capacite(cat, provider_ref: str, model_ref: str, capability: str,
                 ok: bool) -> None:
    """Écrit une observation simple dans `capacite_log` (table LÉGÈRE à deux
    entrées) : {provider, model, capability, ok}. La table d'expérience
    model_endpoint_provider_capacite est mise à jour par BATCH (flush)."""
    try:
        cat.conn.execute("""
            INSERT INTO capacite_log
                (provider_id, model_id, capability, ok, created_at)
            VALUES (
                COALESCE((SELECT id FROM catalogue_providers WHERE ref = ?), 0),
                COALESCE((SELECT id FROM catalogue_models WHERE ref = ?), 0),
                ?, ?, strftime('%s','now'))
        """, (provider_ref, model_ref, capability, 1 if ok else 0))
        cat.conn.commit()
    except Exception:
        pass


def flush_capacite_log(cat, batch_size: int = 1000) -> int:
    """Met à jour model_endpoint_provider_capacite PAR BATCH depuis
    capacite_log (au lieu d'écrire à chaque appel). Retourne le nb d'obs
    traitées."""
    try:
        from modules.sql.catalogue_repo import ModelCapaciteRepository
        rows = cat.conn.execute(
            "SELECT id, provider_id, model_id, capability, ok "
            "FROM capacite_log LIMIT ?", (batch_size,)).fetchall()
        if not rows:
            return 0
        repo = ModelCapaciteRepository(cat.conn)
        for r in rows:
            if not r["provider_id"] or not r["model_id"]:
                continue
            try:
                repo.observe_bool(r["model_id"], 0, r["provider_id"],
                                  r["capability"], bool(r["ok"]),
                                  source="experience", strength=0.3)
            except Exception:
                pass
        cat.conn.execute(
            "DELETE FROM capacite_log WHERE id IN "
            "(SELECT id FROM capacite_log LIMIT ?)", (batch_size,))
        cat.conn.commit()
        return len(rows)
    except Exception:
        return 0
