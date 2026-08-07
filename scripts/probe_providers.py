"""Probe de fiabilité des providers LLM (toutes les clés fournies sont gratuites).

Teste un prompt simple AVEC tool calling sur un modèle candidat par provider,
mesure le succès + latence, et journalise dans model_call_log → le scoring
d'allocation (latence + taux d'erreur) s'auto-corrige.

Règles utilisateur :
- espacer les probes (surtout même fournisseur) ; sleep configurable.
- google : RPM bas mais fenêtres hautes → un seul probe léger suffit.
- openrouter : 50 req/jour TOTALES → ne pas trop en consommer.

Usage:
  python3 scripts/probe_providers.py [--providers kilo,nvidia] [--sleep 3] [--max 2]
"""

import argparse
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

# Modèles candidats fiables par provider (champ libre, testé au plus tôt).
# Chaque entrée : (provider_ref, model_ref). On préfère des modèles légers
# connus pour répondre, avec une fenêtre suffisante pour un prompt d'outils.
CANDIDATES = {
    "kilo": [("kilo", "poolside/laguna-s-2.1:free")],
    "nvidia": [("nvidia", "meta/llama-3.2-11b-vision-instruct"),
               ("nvidia", "meta/llama-3.3-70b-instruct")],
    "google": [("google", "gemini-3.5-flash-lite"),
               ("google", "gemini-3.5-flash")],
    "deepseek": [("deepseek", "deepseek-chat")],
    "cohere": [("cohere", "command-r7b-12-2024")],
    "groq": [("groq", "llama-3.3-70b-versatile"),
             ("groq", "llama-3.1-8b-instant")],
    "opencode-zen": [("opencode-zen", "opencode-zen/deepseek-v4-flash-free")],
    "mistral": [("mistral", "mistral-small-latest")],
    "ollama-cloud": [("ollama-cloud", "llama3.1:8b")],
}

PROBE_SYSTEM = ("Tu es un assistant qui répond UNIQUEMENT via l'outil "
                "probe_reply_v1. Ne réponds jamais en texte libre.")
PROBE_TOOLS = [{
    "type": "function",
    "function": {
        "name": "probe_reply_v1",
        "description": "Répond au probe.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
        },
    },
}]
PROBE_USER = "Appelle probe_reply_v1 avec ok=true. Réponds UNIQUEMENT via l'outil."


def _log_call(cat, provider_ref, model_ref, success, latency_ms, error_code="", error_msg=""):
    """Journalise dans model_call_log pour alimenter le scoring runtime."""
    try:
        cat.conn.execute("""
            INSERT INTO model_call_log
                (provider_id, model_id, provider_model_id, agent_id, success,
                 tokens_in, tokens_out, tokens_thinking, latency_ms,
                 error_code, error_msg, call_type)
            VALUES (
                COALESCE((SELECT id FROM catalogue_providers WHERE ref = ?), 0),
                COALESCE((SELECT id FROM catalogue_models WHERE ref = ?), 0),
                (SELECT pm.id FROM provider_models pm
                  JOIN catalogue_providers p ON p.id = pm.provider_id
                 WHERE p.ref = ? AND pm.provider_model_name = ?),
                'probe', ?, 0, 0, 0, ?, ?, ?, 'probe')
        """, (provider_ref, model_ref, provider_ref, model_ref,
              1 if success else 0, latency_ms, error_code,
              (error_msg or "")[:200]))
        cat.conn.commit()
    except Exception:
        try:
            cat.conn.rollback()
        except Exception:
            pass


def _probe(bridge, provider_ref, model_ref):
    """Probe un modèle : succès + latence + capacité tool calling."""
    t0 = time.time()
    try:
        resp = bridge.chat(
            provider_ref=provider_ref, model_ref=model_ref,
            messages=[{"role": "system", "content": PROBE_SYSTEM},
                      {"role": "user", "content": PROBE_USER}],
            tools=PROBE_TOOLS, temperature=0.0, max_tokens=50,
            stream=False)
        lat = int((time.time() - t0) * 1000)
        tool_calls = getattr(resp, "tool_calls", None)
        has_tools = bool(tool_calls)
        content = getattr(resp, "content", "") or ""
        ok = has_tools or ("probe_reply" in content)
        return {"ok": ok, "latency_ms": lat, "tool_calls": has_tools,
                "content": str(content)[:120]}
    except Exception as e:
        lat = int((time.time() - t0) * 1000)
        err = str(e)[:200]
        err_code = "unknown"
        for kw in ("auth", "401", "invalid key", "api key"):
            if kw in err.lower():
                err_code = "auth"
                break
        for kw in ("rate", "429", "quota"):
            if kw in err.lower():
                err_code = "rate_limit"
                break
        return {"ok": False, "latency_ms": lat, "error": err, "error_code": err_code}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--providers", default="", help="liste comma (kilo,nvidia)")
    ap.add_argument("--sleep", type=int, default=3, help="secondes entre probes")
    ap.add_argument("--max", type=int, default=2, help="max models par provider")
    args = ap.parse_args()

    from modules.sql.db import CatalogueDB
    from modules.llm_manager.llm_manager import LLMManager

    cat = CatalogueDB()
    llm = LLMManager(cat)
    bridge = llm.get_bridge()

    provs = [p.strip() for p in args.providers.split(",") if p.strip()] \
        if args.providers else list(CANDIDATES.keys())

    print(f"Probe de {len(provs)} providers (sleep={args.sleep}s, max={args.max}/prov)")
    results = []
    for prov in provs:
        cands = CANDIDATES.get(prov, [])[:args.max]
        if not cands:
            print(f"  {prov}: pas de candidat défini")
            continue
        for (pr, mr) in cands:
            r = _probe(bridge, pr, mr)
            _log_call(cat, pr, mr, r["ok"], r.get("latency_ms", 0),
                      r.get("error_code", ""), r.get("error", ""))
            status = "OK" if r["ok"] else ("ERR " + r.get("error_code", ""))
            tools = "tools" if r.get("tool_calls") else "no-tools"
            print(f"  {pr}/{mr}: {status} ({r.get('latency_ms',0)}ms) {tools}")
            results.append({**r, "provider": pr, "model": mr})
            time.sleep(args.sleep)  # espacer, surtout même fournisseur

    ok_n = sum(1 for r in results if r["ok"])
    print(f"\n{ok_n}/{len(results)} probes OK")
    return 0 if ok_n == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
