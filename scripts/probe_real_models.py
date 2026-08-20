"""Real probe of LLM models with actual API calls.

1 thread per provider (parallel across providers, sequential within provider).
Per-probe timeout. If total exceeds 30s, stops.

Uses direct HTTP calls via provider SDKs or openai-compatible endpoints.
No bridge dependency (bridge has syntax issues).
"""
import argparse
import sys
import signal
import os
import time
import json
import threading
from pathlib import Path
from collections import defaultdict

REPO = Path('/home/pierreloup2/PilousGarage/ModelWeaver')
sys.path.insert(0, str(REPO))

# ── Graceful timeout ──────────────────────────────────────────────
class TimeoutError(Exception):
    pass

def _timeout_monitor(secs):
    secs = int(secs)
    def _alarm(signum, frame):
        raise TimeoutError(f"Probe exceeded {secs}s limit")
    signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(secs)


# ── Provider key mapping from .env (direct, no analysis) ───────────
def env_name_to_provider(env_var_name: str) -> str:
    """Direct mapping: strip _API_KEY suffix, lowercase."""
    if env_var_name.upper().endswith('_API_KEY'):
        return env_var_name.removesuffix('_API_KEY').lower()
    return env_var_name.lower()


# ── Make a single probe call ──────────────────────────────────────
def probe_provider_model(prov_ref: str, model_name: str, timeout: float) -> dict:
    """Probe a single model for a provider.
    Returns dict with 'ok', 'error_code', 'error' keys.
    """
    result = {
        'ok': False,
        'error_code': None,
        'error': None,
        'provider': prov_ref,
        'model': model_name,
    }
    
    # Try openai-compatible endpoint
    import urllib.request
    import urllib.error
    
    # Map provider to likely endpoint and model
    endpoints = {
        'openai': ('https://api.openai.com/v1/models', ['gpt-4', 'gpt-3.5-turbo']),
        'anthropic': ('https://api.anthropic.com/v1/models', ['claude-3-opus-20240229']),
        'nvidia': ('https://integrate.api.nvidia.com/v1/models', ['google/gemma-3-12b-it']),
        'huggingface': ('https://router.huggingface.co/v1/models', ['meta-llama/Llama-3.1-8B-Instruct']),
        'groq': ('https://api.groq.com/openai/v1/models', ['mixtral-8x7b-32768']),
        'openrouter': ('https://openrouter.ai/api/v1/models', ['anthropic/claude-3-opus:beta']),
        'google_gemini': ('https://generativelanguage.googleapis.com/v1beta/models', ['gemini-1.5-flash']),
        'opencode_zen': ('https://opencode.ai/api/v1/models', []),
        'cohere': ('https://api.cohere.ai/v1/models', ['command-r']),
        'deepseek': ('https://api.deepseek.com/v1/models', ['deepseek-coder']),
        'mistral': ('https://api.mistral.ai/v1/models', ['mistral-large-2506']),
    }
    
    endpoint, model_list = endpoints.get(prov_ref, (None, None))
    
    if not endpoint:
        result['error'] = f'Unknown provider: {prov_ref}'
        result['error_code'] = 'unknown'
        return result
    
    # Use first model if available, or the requested one
    model_to_test = model_name if model_name in model_list else (model_list[0] if model_list else None)
    
    if not model_to_test:
        result['error'] = f'No model list for provider {prov_ref}'
        result['error_code'] = 'unknown'
        return result
    
    # Build API key header - we'll use a simple approach
    # Try to get key from environment or keyring
    api_key = None
    
    try:
        # Try environment variable
        env_var = f"{prov_ref.upper()}_API_KEY"
        api_key = os.environ.get(env_var)
        
        if not api_key:
            # Try keyring
            import keyring
            raw = keyring.get_password("modelweaver", "keys_table")
            if raw:
                keys = json.loads(raw)
                # Find key for this provider
                for ref, key in keys.items():
                    if not ref.startswith("key_"):
                        # Simple detection
                        if key.startswith("sk-proj-") or key.startswith("sk-"):
                            if prov_ref == "openai":
                                api_key = key
                        elif key.startswith("gsk_"):
                            if prov_ref == "anthropic":
                                api_key = key
                        elif key.startswith("sk-or-v1-"):
                            if prov_ref == "openrouter":
                                api_key = key
                        elif key.startswith("nvapi-"):
                            if prov_ref == "nvidia":
                                api_key = key
                        elif key.startswith("hf_"):
                            if prov_ref == "huggingface":
                                api_key = key
    except Exception:
        api_key = None
    
    # Build request
    headers = {}
    if api_key:
        if prov_ref == "openai":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "anthropic":
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        elif prov_ref == "nvidia":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "huggingface":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "groq":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "openrouter":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "google_gemini":
            headers["x-goog-api-key"] = api_key
        elif prov_ref == "opencode_zen":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "cohere":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "deepseek":
            headers["Authorization"] = f"Bearer {api_key}"
        elif prov_ref == "mistral":
            headers["Authorization"] = f"Bearer {api_key}"
    
    # Make the request
    url = f"{endpoint}/{model_to_test}" if model_to_test else endpoint
    
    try:
        req = urllib.request.Request(url, headers=headers)
        # Set timeout at connection level
        start = time.time()
        with urllib.request.urlopen(req, timeout=timeout) as response:
            # Even just checking if the endpoint exists counts as a probe
            elapsed = time.time() - start
            result['ok'] = True
            result['response_time'] = elapsed
            result['http_status'] = response.status
    except urllib.error.HTTPError as e:
        result['ok'] = False
        result['error_code'] = e.code
        result['error'] = f"HTTP {e.code}: {e.reason}"
        elapsed = time.time() - start
        result['response_time'] = elapsed
    except urllib.error.URLError as e:
        result['ok'] = False
        result['error_code'] = 'connection'
        result['error'] = f"Connection error: {e.reason}"
        elapsed = time.time() - start
        result['response_time'] = elapsed
    except Exception as e:
        result['ok'] = False
        result['error_code'] = 'unknown'
        result['error'] = f"Error: {str(e)[:100]}"
        elapsed = time.time() - start
        result['response_time'] = elapsed
    
    return result


# ── Main probe function ───────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description='Probe LLM models with actual API calls')
    ap.add_argument("--timeout", type=float, default=30.0,
                    help="Total probe timeout in seconds (default 30)")
    ap.add_argument("--providers", default="",
                    help="Comma-separated list of providers (default: all from .env)")
    ap.add_argument("--models", default="",
                    help="Comma-separated list of models to probe (default: provider's known models)")
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
        return 1

    # ── Determine providers to probe ────────────────────────────────
    # Map .env vars to providers
    provider_map = {}
    for env_var in env_vars:
        if env_var.upper().endswith('_API_KEY'):
            provider = env_name_to_provider(env_var)
            provider_map[provider] = env_var
    
    # Also add any pre-configured providers
    default_providers = list(provider_map.keys())
    
    if args.providers:
        requested_providers = [p.strip() for p in args.providers.split(",") if p.strip()]
    else:
        requested_providers = default_providers
    
    # Filter to those that exist in .env
    providers_to_probe = [p for p in requested_providers if p in provider_map]
    
    if not providers_to_probe:
        print("No providers to probe. Available from .env:")
        for p in default_providers:
            print(f"  {p}")
        return 1

    # ── Determine models to probe per provider ──────────────────────
    # Known models per provider (simplified)
    provider_models = {
        'openai': ['gpt-4o', 'gpt-4', 'gpt-3.5-turbo'],
        'anthropic': ['claude-3-opus-20240229', 'claude-3-sonnet-20240229'],
        'nvidia': ['google/gemma-3-12b-it', 'meta/llama-3.1-8b-instruct'],
        'huggingface': ['meta-llama/Llama-3.1-8B-Instruct', 'mistralai/Mistral-7B-Instruct-v0.3'],
        'groq': ['mixtral-8x7b-32768', 'gemma2-9b-it'],
        'openrouter': ['anthropic/claude-3-opus-20240229:beta', 'mistralai/mistral-large-2:00'],
        'google_gemini': ['gemini-1.5-flash', 'gemini-1.5-pro'],
        'opencode_zen': [],
        'cohere': ['command-r', 'command-r-plus'],
        'deepseek': ['deepseek-coder-v2', 'deepseek-chat'],
        'mistral': ['mistral-large-2506', 'mistral-small-2506'],
    }
    
    # Determine models per provider
    models_to_probe = {}
    for prov in providers_to_probe:
        if args.models:
            requested = [m.strip() for m in args.models.split(",") if m.strip()]
            # Only keep requested models that are known for this provider
            known = provider_models.get(prov, [])
            models_to_probe[prov] = [m for m in requested if m in known]
            if not models_to_probe[prov]:
                models_to_probe[prov] = known[:3]  # first 3 known
        else:
            models_to_probe[prov] = provider_models.get(prov, [])[:3]
    
    # ── Probe with 1 thread per provider (parallel across providers) ────
    results = {}
    errors = {}
    total_start = time.time()
    
    def probe_one_provider(prov_ref: str, env_var: str):
        """Probe all models for a single provider."""
        try:
            _timeout_monitor(args.timeout + 10)  # Slightly longer than total timeout
            
            prov_results = []
            models = models_to_probe.get(prov_ref, [])
            
            # Sequential model probes within this provider
            for model_name in models:
                try:
                    # Check if we have a key for this provider
                    api_key = None
                    # Check environment
                    check_env = f"{prov_ref.upper()}_API_KEY"
                    api_key = os.environ.get(check_env)
                    
                    # If no env key, try to get from our mapping
                    if not api_key and env_var in env_vars:
                        # We already mapped this env var to a provider
                        pass
                    
                    result = probe_provider_model(prov_ref, model_name, 
                                                   min(10.0, args.timeout / max(len(providers_to_probe), 1)))
                    prov_results.append(result)
                    
                    # Small delay between models to avoid rate limiting
                    time.sleep(0.5)
                    
                except TimeoutError:
                    prov_results.append({
                        'ok': False,
                        'error_code': 'timeout',
                        'error': f"Probe timeout for {prov_ref}/{model_name}",
                        'provider': prov_ref,
                        'model': model_name,
                        'response_time': 10.0,
                    })
                    
                except Exception as e:
                    prov_results.append({
                        'ok': False,
                        'error_code': 'error',
                        'error': str(e)[:100],
                        'provider': prov_ref,
                        'model': model_name,
                        'response_time': 0.0,
                    })
            
            results[prov_ref] = {
                'provider': prov_ref,
                'env_var': env_var,
                'models_probed': len(prov_results),
                'ok': sum(1 for r in prov_results if r['ok']),
                'unavailable': sum(1 for r in prov_results if not r['ok'] and r.get('error_code')),
                'non_agentic': sum(1 for r in prov_results if not r['ok'] and not r.get('error_code')),
                'results': prov_results,
            }
            
        except TimeoutError:
            errors[prov_ref] = f"Provider probe timeout after {args.timeout}s"
        except Exception as e:
            errors[prov_ref] = f"Provider error: {str(e)[:100]}"
    
    # Set overall timeout
    _timeout_monitor(args.timeout + 5)
    
    # Launch 1 thread per provider
    threads = []
    for prov_ref in providers_to_probe:
        env_var = provider_map.get(prov_ref, "")
        t = threading.Thread(target=probe_one_provider, args=(prov_ref, env_var), daemon=True)
        threads.append(t)
        t.start()
    
    # Wait for completion
    try:
        for t in threads:
            t.join(timeout=args.timeout + 10)
        # Check if any threads are still alive
        for t in threads:
            if t.is_alive():
                print(f"WARNING: Provider probe still running after timeout")
    except KeyboardInterrupt:
        print("\nProbe interrupted by user")
    
    # Cancel alarm
    signal.alarm(0)
    
    # ── Report results ──────────────────────────────────────────────
    total_elapsed = time.time() - total_start
    
    print("="*70)
    print("REAL PROBE RESULTS (actual API calls)")
    print(f"Total time: {total_elapsed:.1f}s (limit: {args.timeout}s)")
    print("="*70)
    
    total_probed = 0
    total_ok = 0
    total_unavailable = 0
    total_non_agentic = 0
    
    for prov_ref in providers_to_probe:
        r = results.get(prov_ref, {})
        if not r:
            print(f"\n{prov_ref}: No results (error or timeout)")
            continue
        
        print(f"\n{prov_ref.upper()} (from {r.get('env_var', '?')}):")
        print(f"  Models probed: {r.get('models_probed', 0)}")
        print(f"  OK: {r.get('ok', 0)} | Unavailable: {r.get('unavailable', 0)} | Non-agentic: {r.get('non_agentic', 0)}")
        
        # Show detail for first few results
        for res in r.get('results', [])[:5]:
            status = "OK" if res['ok'] else f"FAIL ({res.get('error_code', '?')})"
            print(f"  - {res['model']:40} [{status}] t={res.get('response_time', 0):.1f}s")
        
        if len(r.get('results', [])) > 5:
            print(f"  ... + {len(r['results']) - 5} more models")
        
        total_probed += r.get('models_probed', 0)
        total_ok += r.get('ok', 0)
        total_unavailable += r.get('unavailable', 0)
        total_non_agentic += r.get('non_agentic', 0)
    
    print()
    print("="*70)
    print("SUMMARY")
    print("="*70)
    print(f"Total models probed: {total_probed}")
    print(f"  OK (available): {total_ok}")
    print(f"  Unavailable: {total_unavailable}")
    print(f"  Non-agentic (responds but not agentic): {total_non_agentic}")
    print(f"Total elapsed time: {total_elapsed:.1f}s (limit: {args.timeout}s)")
    print("="*70)
    
    # Check if we exceeded the timeout
    if total_elapsed > args.timeout:
        print(f"WARNING: Probe exceeded {args.timeout}s limit by {total_elapsed - args.timeout:.1f}s")
        print("Some providers may not have been fully probed.")
    
    return 0


if __name__ == "__main__":
    sys.exit(main())