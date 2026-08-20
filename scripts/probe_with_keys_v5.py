"""Probe models for providers where the user has API keys.

Uses keyring to detect providers, queries info_llm.db for model counts.
No API calls (just catalog lookups).
"""
import argparse
import sys
import signal
from pathlib import Path
import sqlite3

REPO = Path('/home/pierreloup2/PilousGarage/ModelWeaver')
sys.path.insert(0, str(REPO))


def detect_provider(key: str) -> str:
    """Detect provider from API key prefix pattern."""
    if key.startswith("sk-proj-") or key.startswith("sk-"):
        return "openai"
    elif key.startswith("gsk_"):
        return "anthropic"
    elif key.startswith("sk-or-v1-"):
        return "openrouter"
    elif key.startswith("nvapi-"):
        return "nvidia"
    elif key.startswith("hf_"):
        return "huggingface"
    elif key.startswith("AQ."):
        return "together"
    else:
        return "unknown"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="Total probe timeout in seconds (default 30)")
    args = ap.parse_args()

    # ── Load keys from keyring ───────────────────────────────────────
    import keyring
    import json

    raw = keyring.get_password("modelweaver", "keys_table")
    if not raw:
        print("No keys found in keyring.")
        return 0
    keys = json.loads(raw)

    # Map key refs → provider names by prefix/pattern
    prov_counts = {}
    key_details = {}
    for ref, key in keys.items():
        if ref.startswith("key_"):
            continue
        provider = detect_provider(key)
        if provider != "unknown":
            prov_counts[provider] = prov_counts.get(provider, 0) + 1
            key_details.setdefault(provider, []).append((ref, key[:30] + "..."))

    # ── Query info_llm.db for model counts per provider ───────────────
    try:
        conn = sqlite3.connect('modules/sqlite/info_llm/info_llm.db')
        cur = conn.cursor()

        # Get provider info
        cur.execute("SELECT id, ref, name FROM catalogue_providers")
        providers_catalog = cur.fetchall()
        prov_map = {p[1]: p[0] for p in providers_catalog}  # ref -> id

        print(f"Providers with API keys (from keyring): {providers}")
        print(f"  Key counts: {prov_counts}")
        print()

        # For each provider with keys, show model count from catalogue
        for prov in providers:
            prov_key = prov  # e.g., "openai"
            if prov_key in prov_map:
                prov_id = prov_map[prov_key]
                # Count provider models
                cur.execute(
                    "SELECT COUNT(*) FROM provider_models WHERE provider_id = ?",
                    (prov_id,))
                model_count = cur.fetchone()[0]
                print(f"  {prov.upper()} in catalogue:")
                print(f"    - {model_count} provider models")

                # Show first few model names
                cur.execute(
                    "SELECT provider_model_name FROM provider_models "
                    "WHERE provider_id = ? ORDER BY name LIMIT 5",
                    (prov_id,))
                models = [r[0] for r in cur.fetchall()]
                for m in models:
                    print(f"      * {m}")

                # Count catalogue models linked to this provider
                cur.execute(
                    "SELECT COUNT(DISTINCT m.id) FROM catalogue_models m "
                    "JOIN provider_models pm ON pm.model_id = m.id "
                    "WHERE pm.provider_id = ?",
                    (prov_id,))
                cat_model_count = cur.fetchone()[0]
                print(f"    - {cat_model_count} catalogue models")

                # Get capabilities count for first model
                cur.execute(
                    "SELECT COUNT(*) FROM model_capability WHERE model_id = "
                    "(SELECT m.id FROM catalogue_models m "
                    "JOIN provider_models pm ON pm.model_id = m.id "
                    "WHERE pm.provider_id = ? LIMIT 1)",
                    (prov_id,))
                cap_count = cur.fetchone()[0]
                print(f"    - {cap_count} capability entries (sample)")

            else:
                print(f"  {prov.upper()}: not found in catalogue_providers")

        conn.close()

    except Exception as e:
        print(f"  Warning: could not query info_llm.db: {e}")
        print("  (probe completed with keyring data only)")

    print()
    print("="*60)
    print("PROBE COMPLETE")
    print("="*60)

    # Cancel alarm if set
    try:
        signal.alarm(0)
    except:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())