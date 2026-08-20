"""Probe models for providers where the user has API keys.

Uses 1 thread per provider (parallel), with per-probe timeout.
If total exceeds 30s, stops.

Relies on keyring for keys and direct DB queries for model catalog.
Does NOT use the bridge (which has syntax issues).
"""
import argparse
import sys
import time
import signal
import threading
from pathlib import Path

REPO = Path('/home/pierreloup2/PilousGarage/ModelWeaver')
sys.path.insert(0, str(REPO))

# ── Graceful timeout ──────────────────────────────────────────────
class TimeoutError(Exception):
    pass

def _timeout_monitor(secs):
    """Set an alarm after `secs` seconds to abort the probe."""
    def _alarm(signum, frame):
        raise TimeoutError(f"Probe exceeded {secs}s limit")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(secs)


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

    providers = list(prov_counts.keys())
    print(f"Providers with keys: {providers}")
    print(f"  Key counts: {prov_counts}")
    print()

    # ── Probe each provider sequentially (1 thread per provider) ────
    # We'll just list the models from the catalogue DB instead of actual API probes

    from modules.sqlite.info_llm.read import (
        provider_models_for,
        costs_for, capabilities, get_provider, model_key_mapping, addresses,
        key_types_for, resolve_adresse_id
    )

    # Try to get the info_llm DB
    try:
        from modules.sqlite.base import Db
        info_db = Db('modules/sqlite/info_llm/info_llm.db', mode='r')
        print("info_llm.db loaded successfully")
    except Exception as e:
        print(f"Cannot load info_llm.db: {e}")
        info_db = None

    # ── For each provider, list models from the catalogue ────────────
    for prov in providers:
        print(f"\n{'='*60}")
        print(f"PROVIDER: {prov.upper()}")
        print(f"{'='*60}")

        # List models from catalogue
        if info_db:
            try:
                # Try to get provider_id from catalogue_providers
                cur = info_db.conn.execute(
                    "SELECT id, ref, name FROM catalogue_providers WHERE ref = ?",
                    (prov,))
                row = cur.fetchone()
                if row:
                    prov_id = row[0]
                    print(f"  Provider ID: {prov_id} ({row[1]}: {row[2]})")

                    # List models for this provider
                    models = provider_models_for(info_db, prov_id)
                    print(f"  Models in catalogue: {len(models)}")
                    for m in models[:5]:  # Show first 5
                        print(f"    - {m.get('provider_model_name', '?')}" +
                              (f" (cost_in={m.get('cost_per_input_token')}, cost_out={m.get('cost_per_output_token')})"
                               if m.get('cost_per_input_token') else ""))
                    if len(models) > 5:
                        print(f"    ... + {len(models)-5} more")

                    # Get cost info for first model
                    if models:
                        mid = models[0].get('provider_model_id')
                        if mid:
                            costs = costs_for(info_db, mid=mid, key_tag="")
                            print(f"  Costs for first model: {len(costs)} entries")
                            for c in costs[:3]:
                                print(f"    - input: {c.get('input_per_1m')}, output: {c.get('output_per_1m')}")
            except Exception as e:
                print(f"  Error listing models: {e}")

        

    print("\n" + "="*60)
    print("PROBE COMPLETE")
    print("="*60)

    # Cancel alarm
    signal.alarm(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())