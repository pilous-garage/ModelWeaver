"""BridgeRegistry — Chargement dynamique des bridges par provider.

Au lieu d'un seul LiteLLMBridge pour tous les providers, chaque provider
peut avoir son propre bridge python chargé via ``importlib.import_module()``.

Les providers non listés dans ``PROVIDER_BRIDGES`` utilisent DirectBridge
comme fallback universel (appel direct à l'API OpenAI-compatible).

Inspiré de : opencode packages/opencode/src/provider/provider.ts (BUNDLED_PROVIDERS)
"""

import importlib
import logging
from typing import Any, Dict, Optional

from modules.llm_manager.base_bridge import BaseBridge

logger = logging.getLogger("modelweaver.bridge_registry")

PROVIDER_BRIDGES: Dict[str, str] = {
    "openai":    "modules.llm_manager.bridges.openai",
    "anthropic": "modules.llm_manager.bridges.anthropic",
    "google":    "modules.llm_manager.bridges.google",
}

_BRIDGE_CLASS_NAME = "Bridge"


class BridgeRegistry:
    """Registry singleton : choisit et met en cache le bridge pour un provider.

    Usage::

        registry = BridgeRegistry(cat=catalogue, km=key_manager)
        bridge = registry.get("openai")
        response = bridge.chat("openai", "gpt-4o", messages=[...])
    """

    def __init__(self, cat=None, km=None):
        self.cat = cat
        self.km = km
        self._cache: Dict[str, BaseBridge] = {}
        self._fallback = None

    def _load_fallback(self) -> BaseBridge:
        if self._fallback is None:
            from modules.llm_manager.direct_bridge import DirectBridge
            self._fallback = DirectBridge(cat=self.cat, km=self.km)
        return self._fallback

    def get(self, provider_ref: str) -> BaseBridge:
        """Retourne le bridge pour ``provider_ref``.

        1. Cache mémoire → retour immédiat
        2. Module bridge natif dans PROVIDER_BRIDGES → importe et instancie
        3. Fallback DirectBridge
        """
        cached = self._cache.get(provider_ref)
        if cached is not None:
            return cached

        module_path = PROVIDER_BRIDGES.get(provider_ref)
        if module_path is not None:
            bridge = self._import_bridge(module_path, provider_ref)
            if bridge is not None:
                self._cache[provider_ref] = bridge
                return bridge

        fallback = self._load_fallback()
        self._cache[provider_ref] = fallback
        return fallback

    def _import_bridge(self, module_path: str, provider_ref: str) -> Optional[BaseBridge]:
        try:
            mod = importlib.import_module(module_path)
            bridge_cls = getattr(mod, _BRIDGE_CLASS_NAME, None)
            if bridge_cls is None:
                logger.warning("Bridge module %s: no %s class", module_path, _BRIDGE_CLASS_NAME)
                return None
            instance = bridge_cls(cat=self.cat, km=self.km)
            if not isinstance(instance, BaseBridge):
                logger.warning("Bridge %s does not implement BaseBridge", module_path)
                return None
            logger.info("Loaded native bridge for %s from %s", provider_ref, module_path)
            return instance
        except ImportError as exc:
            logger.debug("Native bridge for %s (%s) unavailable: %s", provider_ref, module_path, exc)
            return None
        except Exception as exc:
            logger.warning("Failed to load bridge %s for %s: %s", module_path, provider_ref, exc)
            return None

    def register(self, provider_ref: str, module_path: str, override: bool = False) -> None:
        """Enregistre un bridge natif pour un provider.

        Utile pour les plugins ou bridges chargés tardivement.
        """
        if provider_ref in PROVIDER_BRIDGES and not override:
            logger.warning("Bridge already registered for %s (use override=True to replace)", provider_ref)
            return
        PROVIDER_BRIDGES[provider_ref] = module_path
        self._cache.pop(provider_ref, None)
        logger.info("Registered bridge %s for %s", module_path, provider_ref)

    def available_bridges(self) -> Dict[str, str]:
        """Retourne {provider_ref: module_path} pour les bridges enregistrés."""
        return dict(PROVIDER_BRIDGES)

    def clear(self) -> None:
        """Vide les caches (utile pour les tests)."""
        self._cache.clear()
        self._fallback = None
