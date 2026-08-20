"""Probe models for providers defined in .env file.

Direct mapping: .env VARIABLE_API_KEY → provider "VAR".
No prefix analysis - trusts the .env variable name directly.
Uses str.removesuffix() to strip the _API_KEY suffix.

Usage:
  python3 scripts/probe_with_keys_direct.py --timeout 30
"""
import argparse
import sys
import signal
import os
from pathlib import Path

REPO = Path('/home/pierreloup2/PilousGarage/ModelWeaver')
sys.path.insert(0, str(REPO))

from modules.sqlite.base import Db
from modules.sqlite.paths import db_path
from modules.sqlite.keys.key_manager import KeyManager


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="Total probe timeout in seconds (default 30)")
    args = ap.parse_args()

    # ── Load .env file ───────────────────────────────────────────────
    env_file = os.path.join(REPO, '.env')
    env_vars = {}
    
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, val = line.split('=', 1)
                    env_vars[key.strip()] = val.strip().strip('"').strip("'")
    else:
        print(f".env file not found at {env_file}")
        print()
        print("="*60)
        print("PROBE COMPLETE")
        print("="*60)
        try:
            signal.alarm(0)
        except:
            pass
        return 0

    # ── Load keys into keyring (direct mapping, no analysis) ────────
    km = KeyManager()
    km.load()

    # Direct mapping: each env var XXXX_API_KEY → provider "xxxx"
    # Use removesuffix to correctly strip _API_KEY
    added = []
    for env_var, api_key in env_vars.items():
        if not env_var.upper().endswith('_API_KEY'):
            continue
        
        # Direct mapping: strip _API_KEY suffix, keep the rest, lowercase
        provider = env_var.removesuffix('_API_KEY').lower()
        
        if not api_key or len(api_key) < 10:
            print(f"  Skipping {env_var}: no valid key")
            continue
        
        try:
            ref = km.set_key(provider, api_key, tag='paid', identity='default')
            added.append((provider, env_var, ref))
            print(f"  Mapped {env_var} → {provider}: key loaded")
        except Exception as e:
            print(f"  Error mapping {env_var} → {provider}: {e}")

    # Save state
    km._cache = km.store.load_all()
    km._loaded = True

    # ── Query info_llm.db for model counts ──────────────────────────
    try:
        conn = Db(db_path('info_llm'), mode='ro')
        cur = conn._conn
        
        print()
        print("Probe Results (direct .env mapping, no API calls):")
        print("="*60)
        
        # Count keys per provider from our mapping
        prov_counts = {}
        for provider, env_var, ref in added:
            prov_counts[provider] = prov_counts.get(provider, 0) + 1
        
        # Report per provider from .env mapping
        for provider, count in sorted(prov_counts.items()):
            # Look up provider in catalogue
            try:
                cur.execute("SELECT ref, name FROM catalogue_providers WHERE ref = ?", (provider,))
                row = cur.fetchone()
                if row:
                    prov_name = row[1]
                else:
                    # Try searching by name containing the provider
                    cur.execute("SELECT ref, name FROM catalogue_providers WHERE name LIKE ?", 
                               (f'%{provider}%',))
                    rows = cur.fetchall()
                    if rows:
                        prov_name = rows[0][1]
                    else:
                        prov_name = provider.capitalize()
                print(f"  {provider.upper():15} (from {env_var}): {count} key(s) → catalogue: {prov_name}")
            except Exception as e:
                print(f"  {provider.upper():15} (from {env_var}): {count} key(s) → error: {e}")
        
        # Report catalogue provider count
        try:
            prov_rows = cur.execute("SELECT ref, name FROM catalogue_providers").fetchall()
            print(f"  Catalogue has {len(prov_rows)} providers total")
        except:
            pass
        
        conn.close()
        
    except Exception as e:
        print(f"  Warning: could not query info_llm.db: {e}")
        print("  (probe completed with .env mapping only)")

    print()
    print("="*60)
    print("PROBE COMPLETE (direct .env mapping)")
    print("="*60)

    # Cancel alarm if set
    try:
        signal.alarm(0)
    except:
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())