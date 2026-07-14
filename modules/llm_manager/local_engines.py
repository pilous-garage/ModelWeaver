"""Gestion des moteurs LLM locaux (Ollama, LM Studio, llama.cpp).

Détection live (port ouvert + appel API locale), démarrage/arrêt des
moteurs gérables en headless (Ollama), et listage des modèles disponibles.

Conçu pour être testé en conteneur : aucune dépendance lourde, détection
par socket + HTTP uniquement.
"""

import json
import socket
import subprocess
import time
from typing import Any, Dict, List, Optional

# ── Spécifications des moteurs connus ──────────────────────────────
# api_type : "ollama" (API native) ou "openai" (compatible /v1/models)
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "ollama": {
        "name": "Ollama",
        "default_port": 11434,
        "api_type": "ollama",
        "models_endpoint": "/api/tags",
        "start_cmd": ["ollama", "serve"],
        "process_match": ["ollama serve", "ollama.exe"],
        "headless": True,
    },
    "lmstudio": {
        "name": "LM Studio",
        "default_port": 1234,
        "api_type": "openai",
        "models_endpoint": "/v1/models",
        "start_cmd": None,
        "process_match": ["lmstudio", "LM Studio"],
        "headless": False,
    },
    "llamacpp": {
        "name": "llama.cpp",
        "default_port": 8080,
        "api_type": "openai",
        "models_endpoint": "/v1/models",
        "start_cmd": None,
        "process_match": ["llama-server", "server", "llama.cpp"],
        "headless": False,
    },
}


def _port_open(host: str, port: int, timeout: float = 0.3) -> bool:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        return s.connect_ex((host, port)) == 0
    finally:
        s.close()


def _http_get_json(url: str, timeout: float = 2.0) -> Any:
    import urllib.request
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_models(raw: Any, api_type: str) -> List[Dict[str, str]]:
    """Normalise la réponse d'API en liste de {ref, name}."""
    out: List[Dict[str, str]] = []
    if api_type == "ollama":
        for m in (raw.get("models") or []):
            name = m.get("name")
            if name:
                out.append({"ref": name, "name": name})
    else:  # openai /v1/models → {"data":[{"id": ...}]}
        for m in (raw.get("data") or []):
            mid = m.get("id")
            if mid:
                out.append({"ref": mid, "name": mid})
    return out


class LocalEngine:
    def __init__(self, ref: str, spec: Dict[str, Any],
                 running: bool = False, port: Optional[int] = None,
                 models: Optional[List[Dict[str, str]]] = None,
                 error: Optional[str] = None):
        self.ref = ref
        self.name = spec.get("name", ref)
        self.api_type = spec.get("api_type", "openai")
        self.headless = spec.get("headless", False)
        self.default_port = spec.get("default_port")
        self.running = running
        self.port = port if port is not None else self.default_port
        self.models = models or []
        self.error = error

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ref": self.ref,
            "name": self.name,
            "api_type": self.api_type,
            "headless": self.headless,
            "running": self.running,
            "port": self.port,
            "model_count": len(self.models),
            "models": self.models,
            "error": self.error,
        }


class LocalEngineManager:
    """Détecte et pilote les moteurs LLM locaux."""

    def __init__(self):
        self._procs: Dict[str, subprocess.Popen] = {}

    def detect(self) -> List[LocalEngine]:
        engines: List[LocalEngine] = []
        for ref, spec in ENGINE_SPECS.items():
            port = spec.get("default_port")
            running = _port_open("127.0.0.1", port)
            models: List[Dict[str, str]] = []
            error: Optional[str] = None
            if running:
                try:
                    raw = _http_get_json(
                        f"http://127.0.0.1:{port}{spec['models_endpoint']}")
                    models = _normalize_models(raw, spec.get("api_type", "openai"))
                except Exception as e:
                    error = f"port ouvert mais API injoignable: {e}"
            engines.append(LocalEngine(ref, spec, running, port, models, error))
        return engines

    def list_engines(self) -> Dict[str, Any]:
        engines = self.detect()
        return {
            "status": "ok",
            "count": len(engines),
            "engines": [e.to_dict() for e in engines],
        }

    def list_models(self, engine_ref: str) -> Dict[str, Any]:
        spec = ENGINE_SPECS.get(engine_ref)
        if not spec:
            return {"status": "error", "error": "moteur inconnu", "engine": engine_ref}
        port = spec.get("default_port")
        if not _port_open("127.0.0.1", port):
            return {"status": "error", "error": "moteur non démarré",
                    "engine": engine_ref, "running": False}
        try:
            raw = _http_get_json(
                f"http://127.0.0.1:{port}{spec['models_endpoint']}")
            models = _normalize_models(raw, spec.get("api_type", "openai"))
            return {"status": "ok", "engine": engine_ref,
                    "models": models, "count": len(models)}
        except Exception as e:
            return {"status": "error", "error": str(e),
                    "engine": engine_ref, "running": True}

    def start(self, engine_ref: str) -> Dict[str, Any]:
        spec = ENGINE_SPECS.get(engine_ref)
        if not spec:
            return {"status": "error", "error": "moteur inconnu", "engine": engine_ref}
        if not spec.get("headless"):
            return {"status": "error",
                    "error": "démarrage headless indisponible (lancez le GUI du moteur)",
                    "engine": engine_ref, "headless": False}
        port = spec.get("default_port")
        if _port_open("127.0.0.1", port):
            return {"status": "ok", "engine": engine_ref,
                    "already_running": True, "pid": None}
        cmd = spec.get("start_cmd")
        if not cmd:
            return {"status": "error", "error": "aucune commande de démarrage",
                    "engine": engine_ref}
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True)
            self._procs[engine_ref] = proc
        except FileNotFoundError:
            return {"status": "error",
                    "error": f"exécutable introuvable: {cmd[0]}",
                    "engine": engine_ref}
        except Exception as e:
            return {"status": "error", "error": str(e), "engine": engine_ref}
        # Attente ouverture du port (max 10s)
        for _ in range(50):
            if _port_open("127.0.0.1", port):
                return {"status": "ok", "engine": engine_ref,
                        "started": True, "pid": proc.pid}
            time.sleep(0.2)
        return {"status": "error",
                "error": "démarré mais port non ouvert après 10s",
                "engine": engine_ref, "pid": proc.pid}

    def stop(self, engine_ref: str) -> Dict[str, Any]:
        spec = ENGINE_SPECS.get(engine_ref)
        if not spec:
            return {"status": "error", "error": "moteur inconnu", "engine": engine_ref}
        proc = self._procs.get(engine_ref)
        stopped = False
        if proc is not None and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            stopped = True
            del self._procs[engine_ref]
        # Fallback : tue les processus matchant (démarrés en dehors de nous)
        if not stopped:
            for pat in spec.get("process_match", []):
                try:
                    subprocess.run(["pkill", "-f", pat], check=False,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
                    stopped = True
                except Exception:
                    pass
        return {"status": "ok", "engine": engine_ref,
                "stopped": stopped, "running": _port_open(
                    "127.0.0.1", spec.get("default_port"))}


# Singleton (partagé entre requêtes du daemon)
_local_mgr: Optional[LocalEngineManager] = None


def get_local_engine_manager() -> LocalEngineManager:
    global _local_mgr
    if _local_mgr is None:
        _local_mgr = LocalEngineManager()
    return _local_mgr
