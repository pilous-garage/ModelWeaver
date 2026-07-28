"""Health check — Teste chaque provider configuré et rapporte l'état.

Parcourt les providers avec clé API, essaye un chat minimal, et
rapporte :
  - ✅ OK (répond)
  - ⚠️ Rate limit (contacté mais limité)
  - ❌ Auth (clé invalide/quota épuisé)
  - ❌ Erreur (timeout, serveur)
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.llm_manager.direct_bridge import DirectBridge
from modules.llm_manager.base_bridge import BridgeError, ErrorCategory


def check_provider(bridge: DirectBridge, provider_ref: str,
                   model_ref: str = "") -> dict:
    """Teste un provider avec un chat minimal."""
    # D'abord essayer de lister les modèles
    try:
        models = bridge.list_available_models(provider_ref)
    except Exception as e:
        return {"provider": provider_ref, "status": "error",
                "models": 0, "error": str(e)[:80]}

    if not models:
        return {"provider": provider_ref, "status": "no-models",
                "models": 0}

    # Prendre le premier modèle qui semble être un chat model
    chat_model = model_ref
    if not chat_model:
        for m in models:
            ref = m["ref"]
            if any(x in ref.lower() for x in ("embed", "guard", "whisper")):
                continue
            if "free" in ref.lower() or "instruct" in ref.lower():
                chat_model = ref
                break
        if not chat_model:
            chat_model = models[0]["ref"]

    # Tester un chat minimal
    try:
        r = bridge.chat(provider_ref, chat_model,
                        [{"role": "user", "content": "OK"}],
                        max_tokens=1, temperature=0)
        return {"provider": provider_ref, "status": "ok",
                "model": chat_model, "models": len(models),
                "response": r.content[:50] if r.content else "(empty)"}
    except BridgeError as be:
        if be.category == ErrorCategory.RATE_LIMIT:
            return {"provider": provider_ref, "status": "rate-limit",
                    "model": chat_model, "models": len(models),
                    "error": str(be.message)[:80]}
        return {"provider": provider_ref, "status": "error",
                "models": len(models), "error": str(be.message)[:80],
                "category": be.category.value}
    except Exception as e:
        return {"provider": provider_ref, "status": "error",
                "models": len(models), "error": str(e)[:80]}


def main():
    bridge = DirectBridge()

    providers = ["openai", "nvidia", "groq", "openrouter",
                 "google", "mistral", "huggingface", "github-models",
                 "together", "deepinfra", "ollama"]

    results = []
    for pref in providers:
        r = check_provider(bridge, pref)
        results.append(r)

    # Summary
    print(f"{'Provider':20s} {'Status':12s} Models  Detail")
    print("-" * 60)
    ok = error = rate = no = 0
    for r in results:
        status = r["status"]
        models = r.get("models", 0)
        detail = r.get("model") or r.get("error", "")
        icon = {"ok": "✅", "rate-limit": "⚠️", "error": "❌",
                "no-models": "⏭️"}.get(status, "❓")
        print(f"{icon} {r['provider']:18s} {status:12s} {models:5d}  {detail[:50]}")
        if status == "ok":
            ok += 1
        elif status == "rate-limit":
            rate += 1
        else:
            error += 1

    print("-" * 60)
    print(f"  ✅ {ok} OK  ⚠️ {rate} rate-limited  ❌ {error} errors")
    return 0 if error == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
