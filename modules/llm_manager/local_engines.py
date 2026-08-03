"""Gestion des moteurs LLM locaux (Ollama, LM Studio, llama.cpp).

Détection live (port ouvert + appel API locale), démarrage/arrêt des
moteurs gérables en headless (Ollama), et listage des modèles disponibles.

Conçu pour être testé en conteneur : aucune dépendance lourde, détection
par socket + HTTP uniquement.
"""

import json
import os
import socket
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── Spécifications des moteurs connus ──────────────────────────────
# api_type : "ollama" (API native) ou "openai" (compatible /v1/models)
# formats   : formats de fichier acceptés par le moteur (permet de
#             regrouper les moteurs par type d'entrée dans la GUI).
# hardware_modes : matériel cible au lancement d'un modèle :
#   cpu     → CPU uniquement
#   gpu     → GPU uniquement
#   cpu_gpu → CPU + GPU (offload partiel)
ENGINE_SPECS: Dict[str, Dict[str, Any]] = {
    "ollama": {
        "name": "Ollama",
        "default_port": 11434,
        "api_type": "ollama",
        "models_endpoint": "/api/tags",
        "start_cmd": ["ollama", "serve"],
        "process_match": ["ollama serve", "ollama.exe"],
        "headless": True,
        "formats": ["gguf"],
        "hardware_modes": ["cpu", "gpu", "cpu_gpu"],
    },
    "lmstudio": {
        "name": "LM Studio",
        "default_port": 1234,
        "api_type": "openai",
        "models_endpoint": "/v1/models",
        "start_cmd": None,
        "process_match": ["lmstudio", "LM Studio"],
        "headless": False,
        "formats": ["gguf"],
        "hardware_modes": ["cpu", "gpu", "cpu_gpu"],
    },
    "llamacpp": {
        "name": "llama.cpp",
        "default_port": 8080,
        "api_type": "openai",
        "models_endpoint": "/v1/models",
        "start_cmd": None,
        "process_match": ["llama-server", "server", "llama.cpp"],
        "headless": True,
        "formats": ["gguf"],
        "hardware_modes": ["cpu", "gpu", "cpu_gpu"],
        "server_cmd": ["llama-server"],
    },
}


# Répertoire des modèles locaux téléchargés via le catalogue HF.
# Un fichier GGUF présent ici peut être servi directement par llama.cpp
# (ou importé dans Ollama via `ollama create`).
def _local_models_dir() -> Path:
    from services._common import mw_home
    return mw_home() / "models"


def list_local_gguf_files() -> List[Dict[str, Any]]:
    """Liste les fichiers GGUF dans ~/.modelweaver/models/ (téléchargés)."""
    out: List[Dict[str, Any]] = []
    base = _local_models_dir()
    if not base.exists():
        return out
    for f in sorted(base.rglob("*.gguf")):
        try:
            size_gb = round(f.stat().st_size / (1024 ** 3), 2)
        except Exception:
            size_gb = None
        out.append({
            "ref": f"local:{f.stem}",
            "name": f.name,
            "path": str(f),
            "size_gb": size_gb,
            "source": "local",
        })
    return out


# Serveur llama.cpp : on mémorise le fichier GGUF servi par le process lancé.
_llamacpp_model: Dict[str, str] = {}


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
        self.formats = spec.get("formats", [])
        self.hardware_modes = spec.get("hardware_modes", [])
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
            "formats": self.formats,
            "hardware_modes": self.hardware_modes,
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

    def list_models_grouped(self) -> Dict[str, Any]:
        """Liste des LLM locaux groupés par format de fichier accepté.

        Retourne, pour chaque format (ex. "gguf") : les moteurs qui
        l'acceptent (avec leurs modèles détectés) + les fichiers locaux
        téléchargés via le catalogue HF (servables par llama.cpp).
        """
        engines = self.detect()
        local_gguf = list_local_gguf_files()
        groups: Dict[str, Any] = {}
        for e in engines:
            for fmt in e.formats:
                g = groups.setdefault(fmt, {"format": fmt, "engines": []})
                g["engines"].append({
                    "ref": e.ref, "name": e.name, "running": e.running,
                    "port": e.port, "headless": e.headless,
                    "hardware_modes": e.hardware_modes,
                    "models": e.models, "error": e.error,
                })
        if local_gguf:
            g = groups.setdefault("gguf", {"format": "gguf", "engines": []})
            g["local_gguf"] = local_gguf
            # llama.cpp peut servir les GGUF locaux même si non détecté
            if not any(x["ref"] == "llamacpp" for x in g["engines"]):
                spec = ENGINE_SPECS["llamacpp"]
                g["engines"].append({
                    "ref": "llamacpp", "name": spec["name"],
                    "running": _port_open("127.0.0.1", spec["default_port"]),
                    "port": spec["default_port"], "headless": True,
                    "models": [], "error": None,
                })
        return {"status": "ok",
                "groups": [groups[k] for k in sorted(groups)],
                "count_groups": len(groups)}

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

    def start(self, engine_ref: str, hardware: str = "auto") -> Dict[str, Any]:
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
        env = None
        if engine_ref == "ollama" and hardware == "cpu":
            # Force CPU : masque les GPU visibles par le runtime
            env = dict(os.environ)
            env["CUDA_VISIBLE_DEVICES"] = ""
            env["HIP_VISIBLE_DEVICES"] = ""
        try:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                start_new_session=True, env=env)
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
        _llamacpp_model.pop(engine_ref, None)
        return {"status": "ok", "engine": engine_ref,
                "stopped": stopped, "running": _port_open(
                    "127.0.0.1", spec.get("default_port"))}

    # ── Modèles individuels ──────────────────────────────────────

    def _gguf_path_for(self, model_ref: str) -> Optional[Path]:
        """Résout le chemin d'un fichier GGUF local depuis son ref (local:xxx)."""
        base = _local_models_dir()
        if model_ref.startswith("local:"):
            name = model_ref[len("local:"):]
            for f in base.rglob("*.gguf"):
                if f.stem == name:
                    return f
            return None
        # Sinon, essayer de matcher par nom de fichier
        for f in base.rglob("*.gguf"):
            if f.stem == model_ref or f.name == model_ref:
                return f
        return None

    def start_model(self, engine_ref: str, model_ref: str,
                    hardware: str = "auto") -> Dict[str, Any]:
        """Démarre le moteur si nécessaire PUIS charge le modèle.

        - ollama   : `ollama serve` (si arrêté) + POST /api/load
        - llamacpp : lance llama-server avec le fichier GGUF cible
        - lmstudio : non pilotable en headless

        `hardware` : "cpu", "gpu", "cpu_gpu" ou "auto" (défaut
        du moteur). Le mode demandé doit être listé dans
        `hardware_modes` du moteur, sinon la demande est refusée.
        """
        spec = ENGINE_SPECS.get(engine_ref)
        if not spec:
            return {"status": "error", "error": "moteur inconnu",
                    "engine": engine_ref}
        if engine_ref == "lmstudio":
            return {"status": "error",
                    "error": "LM Studio ne se pilote pas en headless — "
                             "chargez le modèle depuis son interface",
                    "engine": engine_ref}
        modes = spec.get("hardware_modes", [])
        if hardware == "auto":
            hardware = modes[0] if modes else "cpu"
        if hardware not in modes:
            return {"status": "error",
                    "error": f"mode matériel '{hardware}' non supporté par "
                             f"{spec['name']} (supporté: {', '.join(modes)})",
                    "engine": engine_ref, "hardware": hardware}
        port = spec.get("default_port")
        # 1) moteur
        if not _port_open("127.0.0.1", port):
            if engine_ref == "llamacpp":
                gguf = self._gguf_path_for(model_ref)
                if gguf is None:
                    return {"status": "error",
                            "error": f"fichier GGUF introuvable pour {model_ref}",
                            "engine": engine_ref}
                server = spec.get("server_cmd") or ["llama-server"]
                # hardware → offload GPU (lignes -ngl)
                ngl = 0 if hardware == "cpu" else 99
                try:
                    proc = subprocess.Popen(
                        [*server, "-m", str(gguf), "--host", "127.0.0.1",
                         "--port", str(port), "-ngl", str(ngl)],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        start_new_session=True)
                    self._procs["llamacpp"] = proc
                    _llamacpp_model["llamacpp"] = str(gguf)
                except FileNotFoundError:
                    return {"status": "error",
                            "error": "llama-server introuvable (installez llama.cpp)",
                            "engine": engine_ref}
                except Exception as e:
                    return {"status": "error", "error": str(e),
                            "engine": engine_ref}
                for _ in range(50):
                    if _port_open("127.0.0.1", port):
                        return {"status": "ok", "engine": engine_ref,
                                "model": model_ref, "hardware": hardware,
                                "started": True, "pid": proc.pid,
                                "loaded": True}
                    time.sleep(0.2)
                return {"status": "error",
                        "error": "llama-server démarré mais port fermé après 10s",
                        "engine": engine_ref, "pid": proc.pid}
            res = self.start(engine_ref, hardware=hardware)
            if res.get("status") != "ok":
                return res
        # 2) charger le modèle (ollama)
        if engine_ref == "ollama":
            try:
                payload = json.dumps({"model": model_ref}).encode()
                req = urllib.request.Request(
                    f"http://127.0.0.1:{port}/api/load", data=payload,
                    headers={"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=120) as resp:
                    resp.read()
                return {"status": "ok", "engine": engine_ref,
                        "model": model_ref, "hardware": hardware,
                        "loaded": True}
            except Exception as e:
                # Fallback : generate avec keep_alive court (anciennes versions)
                try:
                    payload = json.dumps({"model": model_ref, "prompt": "",
                                          "keep_alive": 5}).encode()
                    req = urllib.request.Request(
                        f"http://127.0.0.1:{port}/api/generate", data=payload,
                        headers={"Content-Type": "application/json"})
                    with urllib.request.urlopen(req, timeout=120) as resp:
                        resp.read()
                    return {"status": "ok", "engine": engine_ref,
                            "model": model_ref, "hardware": hardware,
                            "loaded": True,
                            "via": "generate-fallback"}
                except Exception as e2:
                    return {"status": "error",
                            "error": f"échec chargement modèle: {e2}",
                            "engine": engine_ref, "model": model_ref}

    def stop_model(self, engine_ref: str, model_ref: str) -> Dict[str, Any]:
        """Décharge le modèle (ne touche pas au moteur).

        - ollama : POST /api/generate keep_alive=0
        - llamacpp : un seul modèle par serveur → arrête le serveur
        """
        spec = ENGINE_SPECS.get(engine_ref)
        if not spec:
            return {"status": "error", "error": "moteur inconnu",
                    "engine": engine_ref}
        port = spec.get("default_port")
        if not _port_open("127.0.0.1", port):
            return {"status": "ok", "engine": engine_ref,
                    "model": model_ref, "already_stopped": True}
        if engine_ref == "llamacpp":
            return self.stop("llamacpp")
        try:
            payload = json.dumps({"model": model_ref, "keep_alive": 0}).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/api/generate", data=payload,
                headers={"Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
            return {"status": "ok", "engine": engine_ref,
                    "model": model_ref, "unloaded": True}
        except Exception as e:
            return {"status": "error",
                    "error": f"échec déchargement: {e}",
                    "engine": engine_ref, "model": model_ref}

    def check_resources(self, model_ref: str,
                        size_gb: Optional[float] = None) -> Dict[str, Any]:
        """Vérifie RAM libre vs besoin estimé (~1.3× la taille du fichier).

        Le besoin d'inférence d'un GGUF est grossièrement estimé à
        `taille_fichier × 1.3` (poids + cache KV + surcoût runtime).
        `size_gb` peut être fourni (ex. taille d'un fichier distant du
        catalogue) ; sinon on cherche le fichier local.
        Bloquant : si RAM libre insuffisante, le lancement est refusé.
        """
        import psutil
        vm = psutil.virtual_memory()
        free_ram_gb = round(vm.available / (1024 ** 3), 2)
        if size_gb is None:
            path = self._gguf_path_for(model_ref)
            if path is not None:
                try:
                    size_gb = round(path.stat().st_size / (1024 ** 3), 2)
                except Exception:
                    size_gb = None
        need_ram_gb = round((size_gb or 4.0) * 1.3, 2)
        ok = free_ram_gb >= need_ram_gb
        return {
            "status": "ok" if ok else "error",
            "model": model_ref,
            "need_ram_gb": need_ram_gb,
            "free_ram_gb": free_ram_gb,
            "size_gb": size_gb,
            "enough": ok,
            "message": (f"OK : {need_ram_gb} Go estimés, "
                        f"{free_ram_gb} Go libres")
                        if ok else
                        (f"Ressources insuffisantes : {need_ram_gb} Go estimés "
                         f"mais seulement {free_ram_gb} Go libres"),
        }


# Singleton (partagé entre requêtes du daemon)
_local_mgr: Optional[LocalEngineManager] = None


def get_local_engine_manager() -> LocalEngineManager:
    global _local_mgr
    if _local_mgr is None:
        _local_mgr = LocalEngineManager()
    return _local_mgr
