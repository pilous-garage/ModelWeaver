#!/usr/bin/env python3
"""Docker Manager — Gestion des ressources Docker pour le swarm.

Orchestration métier des conteneurs de test :
  - Registre de caches : images pré-installées (mw-cache/*) réutilisables,
    pour ne pas reconstruire l'environnement à chaque fois.
  - Fork + install : clone un cache dans un conteneur nommé, y installe une
    liste d'outils via l'installeur ModelWeaver, puis commit en image mw-image/*.
  - Association agent (testeur) ↔ conteneur : registre persistant.
  - Conteneurs persistants : le conteneur survit entre les batteries de tests
    (pas de --rm), réutilisé tant que le projet installé reste le même.

Le skill `docker/run@v1` reste l'interface des agents ; ce service est la
couche de gestion des ressources (routes API docker/*, registre, cycle de vie).
"""

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from modules.container_manager.container_manager import ContainerManager
from services._common import mw_home

# Namespaces d'images
CACHE_PREFIX = "mw-cache"
IMAGE_PREFIX = "mw-image"
DEFAULT_CACHE = f"{CACHE_PREFIX}/base:test"

# Registre persistant (JSON simple, vivra dans ~/.modelweaver/docker_registry.json)
REGISTRY_PATH = "docker_registry.json"


class DockerManager:
    def __init__(self):
        self.cm = ContainerManager()
        self._registry: Dict[str, Any] = {}
        self._load_registry()

    # ── Registre persistant ─────────────────────────────────────

    def _registry_file(self) -> Path:
        return mw_home() / REGISTRY_PATH

    def _load_registry(self) -> None:
        p = self._registry_file()
        if p.exists():
            try:
                self._registry = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                self._registry = {}
        self._registry.setdefault("caches", {})      # name -> image tag
        self._registry.setdefault("containers", {})  # container_name -> meta

    def _save_registry(self) -> None:
        p = self._registry_file()
        p.write_text(json.dumps(self._registry, indent=2), encoding="utf-8")

    # ── Caches ──────────────────────────────────────────────────

    def list_caches(self) -> List[Dict[str, str]]:
        """Liste les caches enregistrés + images docker mw-cache/*."""
        images = [i for i in self.cm.list_images() if i.startswith(CACHE_PREFIX)]
        caches = []
        for name, tag in self._registry["caches"].items():
            caches.append({"name": name, "image": tag, "local": tag in images})
        # Les images mw-cache/* non encore enregistrées
        known = set(self._registry["caches"].values())
        for img in images:
            if img not in known:
                caches.append({"name": img, "image": img, "local": True})
        return caches

    def register_cache(self, name: str, image: str) -> Dict[str, Any]:
        """Enregistre un cache (image existante) sous un nom."""
        if not self.cm.image_exists(image):
            return {"ok": False, "error": f"image inconnue: {image}"}
        self._registry["caches"][name] = image
        self._save_registry()
        return {"ok": True, "name": name, "image": image}

    def create_cache(self, name: str, base_image: str = "python:3.12-slim",
                     tools: Optional[List[str]] = None) -> Dict[str, Any]:
        """Crée un cache : fork d'une image + installation d'outils via
        l'installeur ModelWeaver, puis commit.

        Exemple : create_cache("full", base="python:3.12-slim",
                              tools=["pytest", "git"])
        """
        tools = tools or []
        image_tag = f"{CACHE_PREFIX}/{name}:latest"
        ctr_name = f"mw-cache-build-{uuid.uuid4().hex[:8]}"
        try:
            # 1. Conteneur temporaire depuis l'image de base
            ok, msg = self.cm.create(ctr_name, base_image)
            if not ok:
                return {"ok": False, "error": f"create: {msg}"}
            ok, msg = self.cm.start(ctr_name)
            if not ok:
                return {"ok": False, "error": f"start: {msg}"}

            # 2. Installe les outils via l'installeur ModelWeaver DANS le conteneur
            #    (l'installeur télécharge/exécute selon install_method du catalogue).
            #    Fallback pip si l'outil n'est pas au catalogue.
            for tool in tools:
                r = self._install_tool_in(ctr_name, tool)
                if not r.get("ok"):
                    return {"ok": False, "error": f"install {tool}: {r.get('stderr')}",
                            "partial": True}

            # 3. Snapshot en image cache
            ok, msg = self.cm.commit(ctr_name, image_tag)
            if not ok:
                return {"ok": False, "error": f"commit: {msg}"}
            self._registry["caches"][name] = image_tag
            self._save_registry()
            return {"ok": True, "name": name, "image": image_tag}
        finally:
            self.cm.remove(ctr_name, force=True)

    def _install_tool_in(self, ctr_name: str, tool: str) -> Dict[str, Any]:
        """Installe un outil dans le conteneur cible.

        Priorité : recette du catalogue ModelWeaver (Installer), sinon pip.
        """
        # Essai via l'installeur ModelWeaver (installe dans le conteneur)
        try:
            from modules.installer.installer import Installer
            from modules.catalogue.catalogue import get_catalogue_entry
            entry = get_catalogue_entry(tool)
            if entry:
                installer = Installer(container=ctr_name)
                installer.install(entry)
                return {"ok": True}
        except Exception:
            pass
        # Fallback pip (dans le conteneur)
        r = self.cm.exec(ctr_name, ["pip", "install", "-q", tool], timeout=300)
        return r

    # ── Fork + association testeur ──────────────────────────────

    def fork(self, cache_name: str, container_name: Optional[str] = None,
             agent_id: str = "", project_id: str = "",
             volumes: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
        """Fork un cache en un conteneur nommé persistant, associé à un testeur.

        Retourne le conteneur prêt à recevoir des batteries de tests.
        """
        # Le cache peut être un nom enregistré OU une image mw-cache/ brute
        # (image local non encore enregistrée) : le fork (copie de travail
        # pour un testeur/codeur) doit marcher dans les deux cas.
        cache = self._registry["caches"].get(cache_name)
        if not cache:
            if self.cm.image_exists(cache_name):
                cache = cache_name
            else:
                return {"ok": False, "error": f"cache inconnu: {cache_name}"}
        if not self.cm.image_exists(cache):
            return {"ok": False, "error": f"image cache absente: {cache}"}

        cname = container_name or f"mw-tst-{agent_id or 'anon'}-{uuid.uuid4().hex[:6]}"
        ok, msg = self.cm.create(cname, cache, volumes=volumes, workdir="/workspace")
        if not ok:
            return {"ok": False, "error": f"create: {msg}"}
        ok, msg = self.cm.start(cname)
        if not ok:
            return {"ok": False, "error": f"start: {msg}"}

        self._registry["containers"][cname] = {
            "cache": cache_name,
            "agent_id": agent_id,
            "project_id": project_id,
            "created_at": int(time.time()),
            "status": "running",
        }
        self._save_registry()
        return {"ok": True, "container": cname, "image": cache}

    def associate(self, agent_id: str, container_name: str,
                  project_id: str = "") -> Dict[str, Any]:
        """Associe (ou ré-associe) un agent testeur à un conteneur."""
        if not self.cm.exists(container_name):
            return {"ok": False, "error": f"conteneur inconnu: {container_name}"}
        meta = self._registry["containers"].get(container_name, {})
        meta.update({"agent_id": agent_id, "project_id": project_id,
                     "status": self.cm.status(container_name)})
        self._registry["containers"][container_name] = meta
        self._save_registry()
        return {"ok": True, "container": container_name, "agent_id": agent_id}

    def get_for_agent(self, agent_id: str, project_id: str = "") -> Optional[str]:
        """Retourne le conteneur associé à un testeur (si encore valide)."""
        for cname, meta in self._registry["containers"].items():
            if meta.get("agent_id") == agent_id:
                if project_id and meta.get("project_id") != project_id:
                    continue
                if self.cm.exists(cname):
                    return cname
        return None

    # ── Batteries de tests persistantes ─────────────────────────

    def run_tests(self, container_name: str, command: List[str],
                  workdir: str = "/workspace", timeout: int = 600) -> Dict[str, Any]:
        """Exécute une batterie de tests dans un conteneur persistant.

        Le conteneur n'est PAS supprimé après : on peut relancer d'autres
        commandes / batteries tant que le projet installé reste le même.
        Le clone du projet est monté au fork/create (voir fork()).
        """
        if not self.cm.exists(container_name):
            return {"ok": False, "exit_code": -1, "stdout": "",
                    "stderr": f"conteneur inconnu: {container_name}"}
        if self.cm.status(container_name) != "running":
            self.cm.start(container_name)
        return self.cm.exec(container_name, command, workdir=workdir, timeout=timeout)

    def snapshot(self, container_name: str, image_tag: Optional[str] = None) -> Dict[str, Any]:
        """Commit l'état du conteneur en image (sauvegarde d'un état stable)."""
        tag = image_tag or f"{IMAGE_PREFIX}/{container_name}:latest"
        ok, msg = self.cm.commit(container_name, tag)
        if ok:
            meta = self._registry["containers"].get(container_name, {})
            meta["last_snapshot"] = tag
            self._registry["containers"][container_name] = meta
            self._save_registry()
        return {"ok": ok, "image": tag, "msg": msg}

    def release(self, container_name: str) -> Dict[str, Any]:
        """Arrête et supprime un conteneur (libère la ressource)."""
        if not self.cm.exists(container_name):
            return {"ok": False, "error": f"conteneur inconnu: {container_name}"}
        self.cm.stop(container_name, timeout=2)
        self.cm.remove(container_name, force=True)
        self._registry["containers"].pop(container_name, None)
        self._save_registry()
        return {"ok": True}

    def status_all(self) -> Dict[str, Any]:
        """État de tous les conteneurs gérés."""
        out = {}
        for cname, meta in self._registry["containers"].items():
            out[cname] = {**meta, "status": self.cm.status(cname)}
        return {"containers": out, "count": len(out)}


# Singleton
_INSTANCE: Optional[DockerManager] = None


def get_docker_manager() -> DockerManager:
    global _INSTANCE
    if _INSTANCE is None:
        _INSTANCE = DockerManager()
    return _INSTANCE


if __name__ == "__main__":
    dm = get_docker_manager()
    print("caches:", dm.list_caches())
    print("status:", dm.status_all())
