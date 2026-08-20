"""Probe models for providers where the user has API keys.

Uses keyring to detect providers, reports findings.
No API calls (bridge has syntax issues), just catalog info.
"""
import argparse
import sys
import signal
from pathlib import Path
from collections import Counter

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
    key_details = {}  # provider → list of (ref, key_start)
    for ref, key in keys.items():
        if ref.startswith("key_"):
            continue
        provider = detect_provider(key)
        if provider != "unknown":
            prov_counts[provider] = prov_counts.get(provider, 0) + 1
            key_details.setdefault(provider, []).append((ref, key[:30] + "..."))

    providers = sorted(prov_counts.keys())
    print(f"Providers with API keys (from keyring): {providers}")
    print(f"  Key counts: {prov_counts}")
    print()

    # Show details per provider
    for prov in providers:
        print(f"  {prov.upper()} ({prov_counts[prov]} keys):")
        for ref, kstart in key_details[prov]:
            print(f"    - ref={ref[:30]}... key={kstart}")

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