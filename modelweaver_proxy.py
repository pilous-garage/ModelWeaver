#!/usr/bin/env python3
"""ModelWeaver Proxy — smart fallback router with response annotation.

Routes opencode requests through LiteLLM, picks models from fallback.json,
and prepends routing info to every response.

    python modelweaver_proxy.py [--port 8008] [--litellm-port 8000]
"""

import json
import os
import sys
import time
import typing
import urllib.request
import urllib.error
import http.server
from datetime import datetime, timezone
from pathlib import Path
import copy

APP_DIR = Path(__file__).resolve().parent
FALLBACK_FILE = APP_DIR / ".modelweaver" / "fallback.json"
LITELLM_BASE = "http://127.0.0.1:8000"
PROXY_PORT = 8008
TIMEOUT = 120

# Valeurs par défaut pour le backoff (écrasées par fallback.json si présent)
BASE_BACKOFF = 300
MULTIPLIER = 1.5
MAX_BACKOFF = 86400


def load_fallback() -> dict:
    """Loads fallback configuration and state from fallback.json."""
    if FALLBACK_FILE.exists():
        try:
            return json.loads(FALLBACK_FILE.read_text())
        except (json.JSONDecodeError, OSError) as e:
            print(f"Error loading fallback config: {e}", file=sys.stderr)
    return {"order": [], "models": {}, "backoff": {"base": BASE_BACKOFF, "multiplier": MULTIPLIER, "max": MAX_BACKOFF}}

def save_fallback(fb: dict) -> None:
    """Saves fallback configuration and state to fallback.json."""
    FALLBACK_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        json.dump(fb, open(FALLBACK_FILE, 'w', encoding='utf-8'), indent=2, ensure_ascii=False)
    except OSError as e:
        print(f"Error saving fallback config: {e}", file=sys.stderr)

def compute_backoff(consecutive_failures: int, base: int = 300) -> int:
    if consecutive_failures <= 0:
        return 0
    seconds = base * (MULTIPLIER ** (consecutive_failures - 1))
    return min(seconds, MAX_BACKOFF)

def pick_model(fb: dict) -> typing.Optional[str]:
    """Selects the next available model based on order and backoff."""
    now = datetime.now(timezone.utc)
    for key in fb.get("order", []):
        entry = fb.get("models", {}).get(key, {})
        if not entry.get("enabled", True):
            continue
        dtu_str = entry.get("dont_try_until")
        if dtu_str:
            try:
                dtu = datetime.fromisoformat(dtu_str.replace('Z', '+00:00'))
                if dtu > now:
                    continue
            except ValueError:
                pass  # Ignore invalid format
        return key
    return None

def mark_success(fb: dict, key: str, response_time_ms: int) -> None:
    entry = fb.setdefault("models", {}).setdefault(key, {})
    entry["consecutive_failures"] = 0
    entry["dont_try_until"] = None
    entry["last_ok"] = datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z')
    # Update average response time (simple average)
    current_avg = entry.get("avg_response_ms", 0)
    total_tests = entry.get("total_tests", 0)
    entry["avg_response_ms"] = (current_avg * total_tests + response_time_ms) / (total_tests + 1)
    entry["total_tests"] = total_tests + 1
    entry["last_error"] = None
    save_fallback(fb)

def mark_failure(fb: dict, key: str, error: str = "") -> None:
    entry = fb.setdefault("models", {}).setdefault(key, {})
    fails = entry.get("consecutive_failures", 0) + 1
    entry["consecutive_failures"] = fails
    # Use model-specific cooldown if available, else global BASE_BACKOFF
    model_cooldown = entry.get("cooldown_seconds", BASE_BACKOFF)
    backoff = compute_backoff(fails, base=model_cooldown)
    if backoff > 0:
        dtu_ts = datetime.now(timezone.utc).timestamp() + backoff
        entry["dont_try_until"] = datetime.fromtimestamp(dtu_ts, tz=timezone.utc).isoformat().replace('+00:00', 'Z')
    entry["last_error"] = error[:200]  # Truncate error message
    save_fallback(fb)


class ProxyHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # Silence default logging

    def _forward_to_lite(self, model_key: str, body: bytes) -> dict:
        """Forwards request to LiteLLM and returns result."""
        try:
            req_data = json.loads(body)
        except json.JSONDecodeError:
            return {"error": "Invalid JSON body", "status": 400}

        req_data["model"] = model_key
        # Force non-streaming for simplicity, opencode handles it
        req_data["stream"] = False 

        req = urllib.request.Request(
            f"{LITELLM_BASE}/v1/chat/completions",
            data=json.dumps(req_data).encode('utf-8'),
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        start = time.time()
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            resp = e
        except urllib.error.URLError as e:
            return {"error": f"LiteLLM unreachable: {e.reason}",
                    "status": 502, "elapsed": int((time.time() - start) * 1000)}

        elapsed = int((time.time() - start) * 1000)
        try:
            resp_body = resp.read().decode('utf-8')
            data = json.loads(resp_body)
        except json.JSONDecodeError:
            data = {"error": resp_body[:200]}

        if resp.status >= 400:
            err_msg = data.get("error", {}).get("message", resp_body[:200])
            return {"error": err_msg, "status": resp.status,
                    "data": data, "elapsed": elapsed, "actual_model": model_key}

        # Détection réponse vide — si le contenu est vide ou finish_reason=error,
        # on traite comme un échec pour déclencher le fallback
        choices = data.get("choices", [])
        finish_reason = choices[0].get("finish_reason", "") if choices else ""
        content = (choices[0].get("message", {}).get("content", "") if choices else "").strip()
        if not content or finish_reason == "error":
            reason = "empty_content" if not content else f"finish_reason={finish_reason}"
            return {"error": f"Empty or invalid response ({reason})",
                    "status": 502, "elapsed": elapsed, "data": data,
                    "actual_model": model_key}

        return {"status": resp.status, "data": data, "elapsed": elapsed,
                "actual_model": data.get("model", model_key)}

    def _annotate_response(self, data: dict, model_key: str,
                           actual_model: str) -> dict:
        """Prepends routing path to the response content."""
        routing_msg = f"[modelweaver→{actual_model}] "
        if "choices" in data and data["choices"]:
            msg = data["choices"][0].get("message", {})
            content = msg.get("content", "")
            if content:
                msg["content"] = routing_msg + content
        return data

    def _respond(self, status: int, body: dict) -> None:
        """Sends a JSON response."""
        resp_body = json.dumps(body, ensure_ascii=False).encode('utf-8')
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(resp_body)))
        self.end_headers()
        self.wfile.write(resp_body)

    def do_POST(self):
        if self.path != "/v1/chat/completions":
            return self._respond(404, {"error": {"message": "not_found"}})

        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        fb = load_fallback()

        tried_models = []
        while True:
            model_key = pick_model(fb)
            if not model_key:
                path = "(empty fallback.json or all models disabled/in backoff)"
                self._respond(503, {
                    "error": {"message": f"Aucun modèle disponible. Tentés: {path}"},
                })
                return

            tried_models.append(model_key)
            result = self._forward_to_lite(model_key, body)

            if "error" in result:
                error_message = result.get("error", "Unknown error")
                mark_failure(fb, model_key, str(error_message))
                print(f"Model {model_key} failed: {error_message}", file=sys.stderr)
                continue # Try next model

            # Success
            actual_model = result.get("actual_model", model_key)
            mark_success(fb, model_key, result["elapsed"])
            data = self._annotate_response(result["data"], model_key, actual_model)
            self._respond(result["status"], data)
            return

    def do_GET(self):
        if self.path == "/health":
            self._respond(200, {"status": "healthy"})
        elif self.path == "/v1/models":
            fb = load_fallback()
            models_data = []
            for key in fb.get("order", []):
                entry = fb.get("models", {}).get(key, {})
                if entry.get("enabled", True):
                    models_data.append({"id": key, "object": "model"})
            self._respond(200, {"object": "list", "data": models_data})
        else:
            # Forward unknown GET requests to LiteLLM
            try:
                req = urllib.request.Request(f"{LITELLM_BASE}{self.path}", headers={"Content-Type": "application/json"})
                resp = urllib.request.urlopen(req, timeout=10)
                data = json.loads(resp.read())
                self._respond(resp.status, data)
            except Exception as e:
                self._respond(502, {"error": {"message": str(e)}})

def main():
    port = PROXY_PORT
    litellm_port = 8000

    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            if arg.startswith("--port="):
                port = int(arg.split("=", 1)[1])
            elif arg.startswith("--litellm-port="):
                litellm_port = int(arg.split("=", 1)[1])

    global LITELLM_BASE
    LITELLM_BASE = f"http://127.0.0.1:{litellm_port}"

    server = http.server.HTTPServer(("127.0.0.1", port), ProxyHandler)
    print(f"🧭  ModelWeaver Proxy → {LITELLM_BASE} (Port: {port})", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n🛑  Proxy arrêté")
        server.server_close()

if __name__ == "__main__":
    main()
