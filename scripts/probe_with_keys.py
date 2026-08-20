"""Probe models for providers where the user has API keys.

Uses 1 thread per provider (parallel), with per-probe timeout.
If total exceeds 30s, stops.

Usage:
  python3 scripts/probe_with_keys.py
  python3 scripts/probe_with_keys.py --timeout 30
"""
import argparse
import sys
import time
import signal
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

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

    # ── Load providers where user has keys (from keyring) ───────────
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
        else:
            ln = ref.lower()
            if "openai" in ln:
                provider = "openai"
            elif "anthropic" in ln or "claude" in ln:
                provider = "anthropic"
        if provider != "unknown":
            prov_counts[provider] = prov_counts.get(provider, 0) + 1

    providers = list(prov_counts.keys())
    print(f"Providers with keys: {providers}")
    print(f"  Key counts: {prov_counts}")
    print()

    # ── Probe each provider in parallel (1 thread per provider) ────
    from services.api._shared import _get_bridge

    bridge = _get_bridge()

    results = {}
    errors = {}

    def probe_provider(prov, timeout):
        """Probe all models for a single provider."""
        try:
            # Use the bridge probe method with per-probe timeout
            # probe(provider) → all models of that provider
            res = bridge.probe(provider_ref=prov, timeout=min(timeout, 10.0))
            ok = sum(1 for r in res["results"] if r.get("ok"))
            unavailable = sum(1 for r in res["results"] if not r.get("ok")
                              and r.get("error_code"))
            non_agentic = sum(1 for r in res["results"] if not r.get("ok")
                              and not r.get("error_code"))
            results[prov] = {
                "probed": res["probed"],
                "ok_agentic": ok,
                "non_agentic": non_agentic,
                "unavailable": unavailable,
            }
            print(f"  {prov}: {ok} agentic, {non_agentic} non-agentic, "
                  f"{unavailable} unavailable out of {res['probed']} probed")
        except Exception as e:
            errors[prov] = str(e)
            print(f"  {prov}: ERROR - {e}")

    # Set overall timeout
    _timeout_monitor(int(args.timeout) + 5)

    # Launch 1 thread per provider
    threads = []
    per_provider_timeout = max(1.0, args.timeout / max(len(providers), 1))
    for prov in providers:
        t = threading.Thread(
            target=probe_provider, args=(prov, per_provider_timeout),
            daemon=True
        )
        threads.append(t)
        t.start()

    # Wait for completion (with overall timeout)
    try:
        for t in threads:
            t.join(timeout=args.timeout + 10)
        # Check if any thread is still alive
        for t in threads:
            if t.is_alive():
                print(f"WARNING: Provider probe still running after timeout")
    except KeyboardInterrupt:
        print("\nInterrupted by user")

    # Cancel alarm
    signal.alarm(0)

    print()
    print("=" * 50)
    print("SUMMARY")
    print("=" * 50)
    for prov in providers:
        r = results.get(prov, {})
        print(f"{prov:15} | probed: {r.get('probed', 0):3} | "
              f"agentic: {r.get('ok_agentic', 0):3} | "
              f"unavailable: {r.get('unavailable', 0):3}")

    if errors:
        print("\nErrors:")
        for prov, err in errors.items():
            print(f"  {prov}: {err}")

    return 0


if __name__ == "__main__":
    import threading
    sys.exit(main())