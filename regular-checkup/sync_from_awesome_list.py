"""Sync depuis awesome-free-llm-apis/data.json.

Récupère la liste curated des providers free avec leurs modèles,
contextes, limites, et endpoints, et peuple la DB.

Source : https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/data.json
"""

import json, sys, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from modules.sql.db import CatalogueDB

DATA_URL = "https://raw.githubusercontent.com/mnfst/awesome-free-llm-apis/main/data.json"

PROVIDER_REF_MAP = {
    "NVIDIA NIM": "nvidia",
    "Groq": "groq",
    "OpenRouter": "openrouter",
    "GitHub Models": "github-models",
    "Google Gemini": "google",
    "Mistral AI": "mistral",
    "Hugging Face": "huggingface",
    "Cerebras": "cerebras",
    "SambaNova": "sambanova",
    "SiliconFlow": "siliconflow",
    "Cohere": "cohere",
    "OVHcloud AI Endpoints": "ovhcloud",
    "Cloudflare Workers AI": "cloudflare",
    "LLM7.io": "llm7",
    "ModelScope": "modelscope",
    "Kilo Code": "kilo",
    "Aion Labs": "aion",
    "Z AI (Zhipu AI)": "zai",
    "Ollama Cloud": "ollama-cloud",
}


def parse_context(ctx_str: str) -> int:
    """Parse une chaîne de contexte (ex: '128K', '1M', '—') en int."""
    ctx_str = ctx_str.strip().lower()
    if ctx_str in ("—", "-", "", "varies", "shared w/ context"):
        return 0
    try:
        if ctx_str.endswith("m"):
            return int(float(ctx_str[:-1]) * 1_000_000)
        if ctx_str.endswith("k"):
            return int(float(ctx_str[:-1]) * 1_000)
        return int(ctx_str)
    except ValueError:
        return 0


def parse_rate_limit(rl: str) -> dict:
    """Extrait RPM, RPD, TPD depuis une chaîne de rate limit."""
    result = {}
    rl = rl.lower()
    for part in rl.split(","):
        part = part.strip()
        if "rpm" in part:
            try:
                result["rpm"] = int(part.split()[0])
            except (ValueError, IndexError):
                pass
        elif "rpd" in part:
            try:
                result["rpd"] = int(part.split()[0])
            except (ValueError, IndexError):
                pass
        elif "tpd" in part or "tokens/day" in part:
            try:
                result["tpd"] = int(part.split()[0].replace(",", ""))
            except (ValueError, IndexError):
                pass
    return result


def main():
    # Fetch data.json
    print("Fetching awesome-free-llm-apis data.json...")
    try:
        req = urllib.request.Request(DATA_URL, headers={"User-Agent": "ModelWeaver/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        print(f"  ERROR fetching: {e}")
        return 1

    db_path = Path.home() / ".modelweaver" / "catalogue.db"
    if not db_path.exists():
        print("  Catalogue DB not found")
        return 1

    cat = CatalogueDB(str(db_path))
    total = 0

    for provider in data.get("providers", []):
        name = provider.get("name", "")
        pref = PROVIDER_REF_MAP.get(name)
        base_url = provider.get("baseUrl", "")

        if not pref:
            print(f"  [{name}] no mapping, skip")
            continue

        # Upsert provider
        cat.conn.execute("""
            INSERT INTO catalogue_providers (ref, name, provider_type, api_type)
            VALUES (?, ?, 'cloud', 'openai-compatible')
            ON CONFLICT(ref) DO UPDATE SET
                name = COALESCE(NULLIF(EXCLUDED.name, ''), catalogue_providers.name)
        """, (pref, name))

        pid = cat.conn.execute(
            "SELECT id FROM catalogue_providers WHERE ref=?", (pref,)
        ).fetchone()
        if not pid:
            continue
        pid = pid["id"]

        # Upsert endpoint
        if base_url:
            cat.conn.execute("""
                INSERT OR IGNORE INTO provider_endpoints
                    (provider_id, label, endpoint_url, api_type, is_default)
                VALUES (?, ?, ?, 'openai-compatible', 1)
            """, (pid, f"{name} API", base_url))

        # Upsert models
        count = 0
        for m in provider.get("models", []):
            mid = m.get("id")
            if not mid:
                continue
            mname = m.get("name", mid)
            ctx = parse_context(m.get("context", ""))
            rl = parse_rate_limit(m.get("rateLimit", ""))

            cat.conn.execute(
                "INSERT OR IGNORE INTO catalogue_models (ref, name) VALUES (?, ?)",
                (mid, mname),
            )
            cat.conn.execute("""
                INSERT OR IGNORE INTO key_endpoint_models
                    (provider_id, model_id, provider_model_name, declared, available, last_checked_at)
                VALUES (?, (SELECT id FROM catalogue_models WHERE ref=?), ?, 1, 1, strftime('%s','now'))
            """, (pid, mid, mid))

            # Update capabilities if context available
            if ctx:
                cat.conn.execute("""
                    INSERT INTO model_capabilities
                        (model_ref, max_context_tokens, supports_chat, source)
                    VALUES (?, ?, 1, 'awesome-list')
                    ON CONFLICT(model_ref) DO UPDATE SET
                        max_context_tokens = COALESCE(NULLIF(?, 0), model_capabilities.max_context_tokens),
                        source = CASE WHEN model_capabilities.source = 'unknown'
                            THEN 'awesome-list' ELSE model_capabilities.source END
                """, (mid, ctx, ctx))

            count += 1

        cat.conn.commit()
        print(f"  [{pref}] {count} models")
        total += count

    print(f"Total: {total} models from awesome-free-llm-apis")
    return 0


if __name__ == "__main__":
    sys.exit(main())
