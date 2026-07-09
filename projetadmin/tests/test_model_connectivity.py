#!/usr/bin/env python3
"""Découverte et test de connectivité pour tous les modèles via LiteLLM.

Cycle de vie d'un modèle :
  1. Découvert via `opencode models` → créé avec `check: true`, `responded: false`
  2. Testé → si succès : `responded: true`, `check: false`
             si échec : backoff exponentiel (×1.5, base 5min), `check: false`
  3. Disparu de `opencode models` → marqué `disparu: <timestamp>`
  4. Disparu depuis longtemps → supprimé du JSON

Usage:
  python tests/test_model_connectivity.py [--mode test|main] [--parallel N] [--force]
  python tests/test_model_connectivity.py --discover-only   # juste synchro, pas de test
"""

import json
import os
import subprocess
import sys
import time
import threading
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime, timezone, timedelta

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import modelweaver  # noqa: E402
FREE_PROVIDERS = getattr(modelweaver, "FREE_PROVIDERS", set())

APP_DIR = Path(__file__).resolve().parent.parent
SCORES_FILE = APP_DIR / ".modelweaver" / "model_scores.json"
ENV_FILE = APP_DIR / ".env"
ROUTING_MODE_FILE = APP_DIR / ".modelweaver" / "routing_mode"
LITELLM_CONFIG_FILE = APP_DIR / ".modelweaver" / "litellm_config.json"
LITELLM_PROXY_CONFIG_FILE = APP_DIR / ".modelweaver" / "litellm_config.yaml"
LITELLM_URL = "http://127.0.0.1:8000"
TIMEOUT = 120
PARALLEL = 8
DISPARU_RETENTION_DAYS = 30

BACKOFF_BASE_SEC = 300
BACKOFF_MULTIPLIER = 1.5
MAX_BACKOFF_SEC = 86400

PROVIDER_URLS = {
    "opencode-zen": "https://opencode.ai/zen/v1",
    "groq": "https://api.groq.com/openai/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434",
}

PROVIDER_PREFIX_MAP = {
    "opencode": "opencode-zen",
    "groq": "groq",
    "openrouter": "openrouter",
}

# Providers with free keys can test all models (paid models will just fail).
# Providers with paid keys test only :free models to avoid billing.
FREE_PROVIDERS = getattr(modelweaver, "get_free_providers", lambda: set())()


def is_free_model(model_key: str) -> bool:
    """Check if a model is free (name contains :free or /free)."""
    _, _, name = model_key.partition("/")
    return ":free" in name or "/free" in name

ENV_KEY_MAP = {
    "opencode-zen": "OPENCODE_ZEN_API_KEY",
    "groq": "GROQ_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
}

lock = threading.Lock()


def load_env():
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def load_scores():
    if SCORES_FILE.exists():
        try:
            return json.loads(SCORES_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass
    return {"models": {}, "last_discovery": None}


def save_scores(scores):
    SCORES_FILE.parent.mkdir(parents=True, exist_ok=True)
    SCORES_FILE.write_text(json.dumps(scores, indent=2) + "\n")


def _resolve_api_key(env_key: str) -> str:
    val = os.environ.get(env_key, "")
    if val:
        return val
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == env_key:
                return v.strip().strip('"').strip("'")
    return ""


def discover_models() -> dict:
    """Run `opencode models`, return dict mapping provider_id -> list of model names."""
    try:
        result = subprocess.run(
            ["opencode", "models"],
            capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        return {}
    if result.returncode != 0:
        return {}

    by_provider: dict = {}
    for line in result.stdout.strip().splitlines():
        line = line.strip()
        if "/" not in line:
            continue
        prefix, _, name = line.partition("/")
        pid = PROVIDER_PREFIX_MAP.get(prefix)
        if pid is None:
            continue
        if pid not in by_provider:
            by_provider[pid] = []
        by_provider[pid].append(name)
    return by_provider


def reconcile_models(discovered: dict):
    """Merge discovered models into model_scores.json : add new, mark disparu."""
    scores = load_scores()
    now_iso = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    scores["last_discovery"] = now_iso

    current_keys = set()
    for pid, names in discovered.items():
        for name in names:
            key = f"{pid}/{name}"
            current_keys.add(key)
            if key not in scores["models"]:
                scores["models"][key] = {
                    "provider": pid,
                    "check": True,
                    "responded": False,
                    "disparu": None,
                    "discovered_at": now_iso,
                    "total_tests": 0,
                    "successful_tests": 0,
                    "consecutive_failures": 0,
                    "response_times_ms": [],
                    "avg_response_ms": None,
                    "last_response": None,
                    "time_stop_try": None,
                    "last_error": None,
                }

    now_dt = datetime.now(timezone.utc)
    for key, entry in list(scores["models"].items()):
        if key not in current_keys and entry.get("disparu") is None:
            entry["disparu"] = now_iso
            entry["check"] = False
        old = entry.get("disparu")
        if old and key not in current_keys:
            try:
                old_dt = datetime.fromisoformat(old.replace("Z", "+00:00"))
                if (now_dt - old_dt).days >= DISPARU_RETENTION_DAYS:
                    del scores["models"][key]
            except ValueError:
                pass

    save_scores(scores)
    return current_keys


def compute_backoff(consecutive_failures):
    if consecutive_failures <= 0:
        return 0
    seconds = BACKOFF_BASE_SEC * (BACKOFF_MULTIPLIER ** (consecutive_failures - 1))
    return min(seconds, MAX_BACKOFF_SEC)


FALLBACK_FILE = APP_DIR / ".modelweaver" / "fallback.json"
BACKOFF_BASE = 300
BACKOFF_MULT = 1.5
BACKOFF_MAX = 86400


def _generate_fallback_json() -> None:
    """Generate fallback.json: ordered model list + backoff state."""
    scores = load_scores()
    existing = {}
    if FALLBACK_FILE.exists():
        try:
            existing = json.loads(FALLBACK_FILE.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    mode = "test"
    if ROUTING_MODE_FILE.exists():
        mode = ROUTING_MODE_FILE.read_text().strip()
    orders = {
        "test": [["groq", "openrouter"]],
        "main": [["opencode-zen"], ["groq", "openrouter"]],
    }
    flat_order = [pid for group in orders.get(mode, orders["test"]) for pid in group]

    order = []
    models_out = {}
    seen = set()
    for pid in flat_order:
        for key, entry in scores.get("models", {}).items():
            if entry.get("provider", "") != pid:
                continue
            if key in seen:
                continue
            seen.add(key)
            order.append(key)
            old = existing.get("models", {}).get(key, {})
            models_out[key] = {
                "provider": pid,
                "enabled": old.get("enabled", True),
                "consecutive_failures": old.get("consecutive_failures", entry.get("consecutive_failures", 0)),
                "dont_try_until": old.get("dont_try_until", entry.get("time_stop_try")),
                "avg_response_ms": old.get("avg_response_ms", entry.get("avg_response_ms")),
                "total_tests": old.get("total_tests", entry.get("total_tests", 0)),
                "last_ok": old.get("last_ok", None),
                "last_error": old.get("last_error", entry.get("last_error")),
            }

    fb = {"order": order, "models": models_out,
          "backoff": {"base": BACKOFF_BASE, "multiplier": BACKOFF_MULT, "max": BACKOFF_MAX}}
    FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    FALLBACK_FILE.write_text(json.dumps(fb, indent=2, ensure_ascii=False) + "\n")
    print(f"  📄  Fallback.json : {FALLBACK_FILE} ({len(order)} modèles)")


def regenerate_litellm_config():
    """Regenerate LiteLLM JSON config from model_scores.json entries."""
    scores = load_scores()
    model_list = []
    for key, entry in scores["models"].items():
        pid = entry.get("provider", "")
        if pid == "ollama":
            continue
        bare_name = key.split("/", 1)[1] if "/" in key else key
        is_openai = pid == "opencode-zen"
        api_key = _resolve_api_key(ENV_KEY_MAP.get(pid, ""))
        entry_cfg = {
            "model_name": bare_name,
            "litellm_params": {
                "model": f"openai/{bare_name}" if is_openai else f"{pid}/{bare_name}",
                "api_key": api_key,
            },
        }
        if is_openai:
            entry_cfg["litellm_params"]["api_base"] = PROVIDER_URLS["opencode-zen"]
        model_list.append(entry_cfg)

    config = {
        "model_list": model_list,
        "router_settings": {
            "routing_strategy": "simple-shuffle",
            "allowed_fails": 3,
            "num_retries": 2,
            "timeout": 30,
            "cooldown_time": 60,
        },
        "litellm_settings": {
            "set_verbose": True,
            "drop_params": False,
        },
    }

    LITELLM_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LITELLM_CONFIG_FILE.write_text(json.dumps(config, indent=2) + "\n")

    lines = ["model_list:"]
    for model in model_list:
        lines.append(f"  - model_name: {model['model_name']}")
        lines.append("    litellm_params:")
        for k, v in model["litellm_params"].items():
            lines.append(f"      {k}: {v}")
    lines.append("router_settings:")
    for k, v in config["router_settings"].items():
        lines.append(f"  {k}: {v}")
    lines.append("litellm_settings:")
    for k, v in config["litellm_settings"].items():
        lines.append(f"  {k}: {v}")

    LITELLM_PROXY_CONFIG_FILE.write_text("\n".join(lines) + "\n")

    print(f"  📄  Config LiteLLM : {LITELLM_CONFIG_FILE}")
    print(f"  📄  Proxy config    : {LITELLM_PROXY_CONFIG_FILE}")
    print(f"  🔢  Modèles         : {len(model_list)}")

    # Generate fallback.json for the proxy
    _generate_fallback_json()


def get_routing_order():
    if ROUTING_MODE_FILE.exists():
        mode = ROUTING_MODE_FILE.read_text().strip()
    else:
        mode = "test"
    orders = {
        "test": ["groq", "openrouter", "ollama", "opencode-zen"],
        "main": ["opencode-zen", "groq", "openrouter", "ollama"],
    }
    return orders.get(mode, orders["test"])


def test_single_model(key: str):
    scores = load_scores()
    entry = scores["models"].get(key, {})
    pid = entry.get("provider", "")
    bare_name = key.split("/", 1)[1] if "/" in key else key

    tst = entry.get("time_stop_try")
    if tst and not entry.get("check"):
        try:
            tst_dt = datetime.fromisoformat(tst.replace("Z", "+00:00"))
            if tst_dt > datetime.now(timezone.utc):
                return
        except ValueError:
            pass

    start = time.time()
    success = False
    response_time_ms = 0
    error_msg = ""

    payload = json.dumps({
        "model": bare_name,
        "messages": [{"role": "user", "content": "hi"}],
        "max_tokens": 5,
    }).encode()

    req = urllib.request.Request(
        f"{LITELLM_URL}/v1/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        response_time_ms = int((time.time() - start) * 1000)
        body = resp.read().decode()
        data = json.loads(body)

        if "choices" in data and data["choices"]:
            content = data["choices"][0].get("message", {}).get("content", "") or ""
            content = content.strip()
            if len(content) >= 1 and not content.startswith("{"):
                success = True
            else:
                error_msg = f"réponse incohérente: {content[:50]}"
        elif "error" in data:
            error_msg = data["error"].get("message", str(data["error"]))[:80]
        else:
            error_msg = f"réponse inattendue: {str(data)[:80]}"

    except urllib.error.HTTPError as e:
        response_time_ms = int((time.time() - start) * 1000)
        try:
            err_body = e.read().decode()
            err_data = json.loads(err_body)
            error_msg = err_data.get("error", {}).get("message", str(e))[:80]
        except Exception:
            error_msg = f"HTTP {e.code}: {str(e)[:80]}"
    except urllib.error.URLError as e:
        error_msg = f"URLError: {str(e.reason)[:80]}"
    except TimeoutError:
        error_msg = f"timeout après {TIMEOUT}s"
    except json.JSONDecodeError:
        error_msg = "réponse non-JSON"
    except Exception as e:
        error_msg = f"{type(e).__name__}: {str(e)[:80]}"

    with lock:
        scores = load_scores()
        e = scores["models"].get(key, {
            "provider": pid, "check": True, "responded": False,
            "disparu": None,
            "total_tests": 0, "successful_tests": 0,
            "consecutive_failures": 0, "response_times_ms": [],
            "avg_response_ms": None, "last_response": None,
            "time_stop_try": None, "last_error": None,
            "discovered_at": None,
        })

        e["check"] = False
        e["total_tests"] = e.get("total_tests", 0) + 1

        if success:
            e["successful_tests"] = e.get("successful_tests", 0) + 1
            e["consecutive_failures"] = 0
            e["last_response"] = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            e["time_stop_try"] = None
            e["last_error"] = None
            e["responded"] = True
            e["disparu"] = None
            times = e.get("response_times_ms", [])
            times.append(response_time_ms)
            if len(times) > 50:
                times = times[-50:]
            e["response_times_ms"] = times
            e["avg_response_ms"] = sum(times) // len(times)
            line = f"  ✅ {key:55s} {response_time_ms}ms"
        else:
            e["consecutive_failures"] = e.get("consecutive_failures", 0) + 1
            e["last_error"] = error_msg
            cf = e["consecutive_failures"]
            backoff_sec = compute_backoff(cf)
            stop_try = datetime.now(timezone.utc) + timedelta(seconds=backoff_sec)
            e["time_stop_try"] = stop_try.isoformat().replace("+00:00", "Z")
            line = f"  ❌ {key:55s} {error_msg} (backoff {backoff_sec}s)"

        scores["models"][key] = e
        save_scores(scores)

    print(line)


def main():
    load_env()
    import argparse
    parser = argparse.ArgumentParser(description="Découverte et test de connectivité des modèles")
    parser.add_argument("--mode", choices=["test", "main"], default=None,
                        help="Ordre de routage")
    parser.add_argument("--parallel", type=int, default=PARALLEL,
                        help=f"Parallélisme (défaut: {PARALLEL})")
    parser.add_argument("--force", action="store_true",
                        help="Ignorer backoff et forcer les tests")
    parser.add_argument("--allow-paid", action="store_true",
                        help="Tester aussi les modèles payants (risque de facturation)")
    parser.add_argument("--discover-only", action="store_true",
                        help="Découverte seule, pas de test")
    parser.add_argument("--limit", type=int, default=0,
                        help="Tester seulement les N premiers modèles (défaut: tous)")
    args = parser.parse_args()

    mode = args.mode
    if not mode and ROUTING_MODE_FILE.exists():
        mode = ROUTING_MODE_FILE.read_text().strip()
    if not mode:
        mode = "test"

    print(f"\n{'='*60}")
    print(f"  🔍  Découverte des modèles via `opencode models`...")
    print(f"{'='*60}")

    discovered = discover_models()
    if not discovered:
        print("  ⚠️  Aucun modèle découvert (opencode pas installé ou injoignable)")
        return 1

    total = sum(len(n) for n in discovered.values())
    for pid, names in sorted(discovered.items()):
        print(f"  • {pid:14s} {len(names):4d} modèles")
    print(f"  ──────────────────────────")
    print(f"  Total : {total} modèles")

    print(f"\n{'='*60}")
    print(f"  🔄  Synchronisation avec model_scores.json...")
    print(f"{'='*60}")
    current_keys = reconcile_models(discovered)

    scores = load_scores()
    before = len(scores["models"])
    new_count = sum(1 for k in current_keys if k not in scores["models"])
    disparu_count = sum(1 for k, v in scores["models"].items() if v.get("disparu"))
    print(f"  📄  Entrées dans model_scores.json : {before}")
    print(f"  🆕  Nouveaux modèles : {new_count}")
    print(f"  💀  Modèles disparus  : {disparu_count}")

    if args.discover_only:
        print(f"\n✅  Découverte terminée. Utilisez --force pour tester.")
        return 0

    # Regenerate LiteLLM config with all discovered models
    print(f"\n{'='*60}")
    print(f"  ⚙️  Régénération de la config LiteLLM ({total} modèles)...")
    print(f"{'='*60}")
    regenerate_litellm_config()

    # Restart LiteLLM with the new config
    print(f"\n  🔄  Redémarrage de LiteLLM avec la nouvelle config...")
    for sig in ("TERM", "KILL"):
        try:
            subprocess.run(
                ["pkill", "-sig", sig, "-f", "litellm.*--port 8000"],
                capture_output=True, timeout=5,
            )
            if sig == "TERM":
                time.sleep(2)
        except Exception:
            pass
    log_path = APP_DIR / ".modelweaver" / "litellm_proxy.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.Popen(
            ["litellm", "--host", "127.0.0.1", "--port", "8000",
             "--config", str(LITELLM_PROXY_CONFIG_FILE), "--detailed_debug"],
            stdout=log_path.open("a", encoding="utf-8"),
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    except Exception as e:
        print(f"      ⚠️  Impossible de démarrer LiteLLM : {e}")
        return 1
    for _ in range(30):
        time.sleep(1)
        try:
            urllib.request.urlopen(f"{LITELLM_URL}/health/readiness", timeout=2)
            print(f"      ✅  Proxy LiteLLM prêt")
            break
        except urllib.error.URLError:
            continue
    else:
        print(f"      ⚠️  LiteLLM pas encore prêt (timeout)")
        return 1

    # Gather models to test
    models_to_test = []
    allow_paid = args.allow_paid
    for key, entry in scores["models"].items():
        if entry.get("disparu"):
            continue

        pid = entry.get("provider", "")
        pid_free = pid in FREE_PROVIDERS

        # Skip paid models if key is paid and not explicitly allowed
        if not pid_free and not allow_paid and not is_free_model(key):
            continue

        if args.force:
            entry["time_stop_try"] = None
            entry["check"] = True
            models_to_test.append(key)
        elif entry.get("check"):
            models_to_test.append(key)
        else:
            tst = entry.get("time_stop_try")
            if tst:
                try:
                    tst_dt = datetime.fromisoformat(tst.replace("Z", "+00:00"))
                    if tst_dt <= datetime.now(timezone.utc):
                        models_to_test.append(key)
                except ValueError:
                    models_to_test.append(key)

    order = get_routing_order()
    priority = {pid: i for i, pid in enumerate(order)}

    def sort_key(key):
        pid = scores["models"].get(key, {}).get("provider", "")
        return (priority.get(pid, 99), key)

    models_to_test.sort(key=sort_key)

    if not models_to_test:
        print(f"\n  ✅  Tous les modèles sont à jour. Rien à tester.")
        return 0

    if args.limit and len(models_to_test) > args.limit:
        models_to_test = models_to_test[:args.limit]

    paid_count = sum(1 for k in models_to_test
                     if not (scores["models"].get(k, {}).get("provider", "") in FREE_PROVIDERS))
    if paid_count:
        print(f"\n  ⚠️  {paid_count} modèle(s) payant(s) inclus"
              f"{' (clé gratuite, sans risque)' if allow_paid else ' (utilisez --allow-paid pour forcer)'}")
    else:
        print(f"\n  ✅  Tous les modèles sont sur des providers à clé gratuite.")

    print(f"\n{'='*60}")
    print(f"  🧪  Test de {len(models_to_test)} modèle(s) (parallélisme: {args.parallel})")
    print(f"{'='*60}\n")

    sem = threading.Semaphore(args.parallel)

    def worker(key):
        sem.acquire()
        try:
            test_single_model(key)
        finally:
            sem.release()

    threads = []
    for key in models_to_test:
        t = threading.Thread(target=worker, args=(key,), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    scores = load_scores()
    print(f"\n{'='*60}")
    print(f"  📊  Résumé")
    print(f"{'='*60}")

    sorted_models = sorted(
        scores["models"].items(),
        key=lambda kv: (
            kv[1].get("consecutive_failures", 0) > 0,
            kv[1].get("avg_response_ms") or 999999,
        ),
    )

    responded = sum(1 for k, v in sorted_models if v.get("responded"))
    failed_recent = sum(1 for k, v in sorted_models
                        if not v.get("responded") and v.get("consecutive_failures", 0) > 0)
    untested = sum(1 for k, v in sorted_models if v.get("check"))
    disparu = sum(1 for k, v in sorted_models if v.get("disparu"))

    print(f"  ✅  Ont répondu : {responded}")
    print(f"  ❌  Échecs       : {failed_recent}")
    print(f"  ⏳  Non testés   : {untested}")
    print(f"  💀  Disparus     : {disparu}")
    print(f"  📁  Scores       : {SCORES_FILE}")
    print()

    return 0


if __name__ == "__main__":
    sys.exit(main())
