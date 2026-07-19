"""Gestionnaire de configuration global (singleton, reload à chaud).

Centralise les variables globales réglables (limites RAM/HD, intervalles,
bornes de tables) dans un fichier JSON sous mw_home (config.json). Accessible
aussi bien par le daemon que par un futur panneau de paramètres GUI, sans
couplage : c'est un module autonome.

Usage :
    from modules.config.config_manager import config
    ram = config.get("usage.ram_buffer_bytes", 10 * 1024 * 1024)
    config.set("usage.ram_buffer_bytes", 20 * 1024 * 1024)  # persisté + notifie
    config.reload()  # relit le disque (ex: modif manuelle ou GUI externe)

Les clés sont en notation pointée ("section.sous_section.champ").
"""

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from services._common import mw_home


CONFIG_FILENAME = "config.json"


# ── Défauts (source unique de vérité) ──────────────────────────────────
DEFAULTS: Dict[str, Any] = {
    # Limites du journal d'usage disque (cf. modules/usage)
    "usage.ram_buffer_bytes": 10 * 1024 * 1024,        # 10 Mo en RAM
    "usage.archive_max_bytes": 100 * 1024 * 1024,      # 100 Mo en HD (par archive)
    "usage.max_archives": 10,                          # nb max d'archives conservées
    "usage.fsync_per_line": True,                      # fsync par ligne (survit crash)
    "usage.collector_poll_seconds": 5.0,               # intervalle rassembleur
    "usage.agent_actif_max_rows": 1000,                # borne taille table agent_actif
    # Général
    "general.heartbeat_timeout_seconds": 3600,         # (réservé, futur sweep)
}


class ConfigManager:
    """Singleton de configuration avec reload à chaud + callbacks."""

    _instance: Optional["ConfigManager"] = None
    _lock = threading.RLock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._init()
        return cls._instance

    def _init(self):
        self._data: Dict[str, Any] = dict(DEFAULTS)
        self._callbacks: List[Callable[[], None]] = []
        self._path = mw_home() / CONFIG_FILENAME
        self._load()

    # ── Accès ──
    def get(self, key: str, default: Any = None) -> Any:
        """Lit une valeur (dotted key). Retombe sur DEFAULTS puis `default`."""
        with self._lock:
            if key in self._data:
                return self._data[key]
        if key in DEFAULTS:
            return DEFAULTS[key]
        return default

    def set(self, key: str, value: Any, persist: bool = True) -> None:
        """Définit une valeur (en mémoire + disque si persist). Notifie."""
        with self._lock:
            self._data[key] = value
            if persist:
                self._save()
        self._notify()

    def as_dict(self) -> Dict[str, Any]:
        """Copie du dictionnaire effectif (defaults écrasés par overrides)."""
        with self._lock:
            merged = dict(DEFAULTS)
            merged.update(self._data)
            return dict(merged)

    # ── Persistance ──
    def _load(self) -> None:
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    with self._lock:
                        self._data.update(raw)
        except Exception:
            # config corrompue -> on garde les defauts
            pass

    def _save(self) -> None:
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # on ne sauvegarde que les overrides (pas les defaults) pour
            # garder le fichier lisible et la source de vérité dans DEFAULTS.
            with self._lock:
                payload = dict(self._data)
            self._path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        except Exception:
            pass

    def reload(self) -> None:
        """Relit le disque (ex: modif manuelle / GUI externe). Notifie."""
        self._load()
        self._notify()

    # ── Hot-reload callbacks ──
    def register_callback(self, cb: Callable[[], None]) -> None:
        with self._lock:
            self._callbacks.append(cb)

    def _notify(self) -> None:
        with self._lock:
            cbs = list(self._callbacks)
        for cb in cbs:
            try:
                cb()
            except Exception:
                pass


# Singleton partagé
config = ConfigManager()
