"""Probe models for providers where the user has API keys.

Simple version: just list providers with keys from keyring.
No DB probing (avoids needing regenerated info_llm.db).
"""
import argparse
import sys
import signal
from pathlib import Path

REPO = Path('/home/pierreloup2/PilousGarage/ModelWeaver')
sys.path.insert(0, str(REPO))


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
    for ref, key in keys.items():
        if ref.startswith("key_"):
            continue
        provider = "unknown"
        if key.startswith("sk-proj-") or key.startswith("sk-"):
            provider = "openai"
        elif key.startswith("gsk_"):
            provider = "anthropic"
        elif key.startswith("sk-or-v1-"):
            provider = "openrouter"
        elif key.startswith("nvapi-"):
            provider = "nvidia"
        elif key.startswith("hf_"):
            provider = "huggingface"
        elif key.startswith("AQ."):
            provider = "together"
        if provider != "unknown":
            prov_counts[provider] = prov_counts.get(provider, 0) + 1

    providers = sorted(prov_counts.keys())
    print(f"Providers with API keys (from keyring): {providers}")
    print(f"  Key counts: {prov_counts}")
    print()

    # Report per-provider key distribution
    for prov in providers:
        count = prov_counts[prov]
        # Give some detail about key types
        prov_keys = [k for ref, k in keys.items()
                     if not ref.startswith("key_") and
                     ((key.startswith("sk-proj-") or key.startswith("sk-"))
                      if prov == "openai"
                      else (key.startswith("gsk_") if prov == "anthropic"
                            else (key.startswith("sk-or-v1-") if prov == "openrouter"
                                  else (key.startswith("nvapi-") if prov == "nvidia"
                                        else (key.startswith("hf_") if prov == "huggingface"
                                              else key.startswith("AQ."))))))]
        print(f"  {prov}: {count} keys")

    print()
    print("="*60)
    print("PROBE COMPLETE (keyring-only, no API calls)")
    print("="*60)

    # Cancel alarm if set
    try:
        signal.alarm(0)
    except:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())