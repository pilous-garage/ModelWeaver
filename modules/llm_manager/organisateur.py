"""Organisateur — Allocation LLM pour les agents (Phase 3).

Logique PURE d'allocation : reçoit les ressources déclarées d'un agent et
retourne le provider/model à utiliser, en fonction des providers disponibles
(clés KeyManager + moteurs locaux) et des recommandations du catalogue.

PRINCIPE : le LLM est un TOOL PROVIDER. L'Organisateur l'assigne au runtime ;
l'agent ne choisit jamais son propre modèle.
"""

from typing import Dict, Any, Optional, List


class Organisateur:
    """Allocation LLM déterministe pour un agent donné ses ressources."""

    def __init__(self):
        self._km = None
        self._llm = None
        self._local = None

    # ── Lazy loaders (pour ne pas ouvrir les DB à l'import) ──

    def _key_manager(self):
        if self._km is None:
            from modules.key_manager.key_manager import KeyManager
            from services._common import _db_paths
            from modules.sql.db import ModelWeaverDB
            self._km = KeyManager(db=ModelWeaverDB(_db_paths()[0]))
        return self._km

    def _llm_manager(self):
        if self._llm is None:
            from modules.llm_manager.llm_manager import LLMManager
            from services._common import _db_paths
            from modules.sql.db import CatalogueDB
            self._llm = LLMManager(cat=CatalogueDB(_db_paths()[1]))
        return self._llm

    def _local_manager(self):
        if self._local is None:
            from modules.llm_manager.local_engines import get_local_engine_manager
            self._local = get_local_engine_manager()
        return self._local

    # ── Sources de disponibilité ──

    def available_providers(self) -> List[str]:
        """Providers avec clé API onboardée (KeyManager)."""
        try:
            return self._key_manager().list_providers()
        except Exception:
            return []

    def running_local_engines(self) -> List[str]:
        """Refs des moteurs locaux détectés ET actifs (ex: ollama)."""
        try:
            engines = self._local_manager().detect()
            return [e.ref for e in engines if e.running]
        except Exception:
            return []

    def _provider_available(self, provider: str) -> bool:
        return provider in self.available_providers() or provider in self.running_local_engines()

    def _pick_model(self, provider: str, use_case: str, level: Optional[str]) -> Optional[str]:
        """Choisit un modèle pour un provider donné (recommandation sinon 1er)."""
        try:
            models = self._llm_manager().list_models(provider)
            if not models:
                # Moteur local : modèles servis par l'API du moteur
                if provider in self.running_local_engines():
                    loc = self._local_manager().list_models(provider)
                    if loc.get("ok") and loc.get("models"):
                        return loc["models"][0].get("ref") or loc["models"][0].get("name")
                return None
            # Filtrer par use_case si pertinent (target_use contient le use_case)
            if use_case:
                matched = [m for m in models if use_case.lower() in (m.get("target_use") or "").lower()]
                if matched:
                    return matched[0]["ref"]
            return models[0]["ref"]
        except Exception:
            return None

    def _recommend(self, use_case: str, level: Optional[str]) -> Optional[Dict[str, Any]]:
        """Recommandation respectant les providers/engins réellement dispo."""
        avail = set(self.available_providers()) | set(self.running_local_engines())
        try:
            recs = self._llm_manager().recommend(use_case, level or "free")["recommendations"]
        except Exception:
            recs = []
        for r in recs:
            if r.get("provider") in avail:
                return {
                    "provider_ref": r["provider"],
                    "model_ref": r["ref"],
                    "reason": r.get("reason", ""),
                }
        # Pas de match de niveau : n'importe quel provider dispo
        if avail:
            p = sorted(avail)[0]
            m = self._pick_model(p, use_case, level)
            if m:
                return {"provider_ref": p, "model_ref": m, "reason": "fallback-available"}
        return None

    # ── Allocation principale ──

    def allocate(self, resources: Dict[str, Any]) -> Dict[str, Any]:
        """Alloue un provider/model pour un agent.

        resources peut contenir :
          - llm: bool            (requiert-il un LLM ?)
          - llm_pref: {provider, model, use_case, level}
              use_case: coding|chat|analysis|writing
              level:    free|paid|local

        Retourne toujours un dict :
          {allocated, provider_ref, model_ref, source, reason, available_providers}
        """
        out_base = {
            "available_providers": self.available_providers(),
            "running_local": self.running_local_engines(),
        }
        if not resources or not resources.get("llm"):
            return {"allocated": False, "reason": "agent_needs_no_llm",
                    "provider_ref": None, "model_ref": None, "source": None, **out_base}

        pref = resources.get("llm_pref") or {}
        provider = pref.get("provider")
        model = pref.get("model")
        use_case = pref.get("use_case", "chat")
        level = pref.get("level")

        # 1. provider + model explicites
        if provider and model:
            if self._provider_available(provider):
                return {"allocated": True, "provider_ref": provider, "model_ref": model,
                        "source": "explicit", "reason": "", **out_base}
            return {"allocated": False, "reason": "preferred_provider_unavailable",
                    "provider_ref": provider, "model_ref": model, "source": "explicit", **out_base}

        # 2. provider explicite -> choisir un modèle
        if provider:
            if self._provider_available(provider):
                m = self._pick_model(provider, use_case, level)
                if m:
                    return {"allocated": True, "provider_ref": provider, "model_ref": m,
                            "source": "provider_preferred", "reason": "", **out_base}
                return {"allocated": False, "reason": "no_model_for_provider",
                        "provider_ref": provider, "model_ref": None, "source": "provider_preferred", **out_base}
            return {"allocated": False, "reason": "preferred_provider_unavailable",
                    "provider_ref": provider, "model_ref": None, "source": "provider_preferred", **out_base}

        # 3. recommandation selon use_case/level
        rec = self._recommend(use_case, level)
        if rec:
            return {"allocated": True, "source": "recommended", "reason": rec.get("reason", ""), **rec, **out_base}

        # 4. fallback dernier ressort
        if self.available_providers() or self.running_local_engines():
            p = (self.available_providers() or self.running_local_engines())[0]
            m = self._pick_model(p, use_case, level)
            if m:
                return {"allocated": True, "provider_ref": p, "model_ref": m,
                        "source": "fallback", "reason": "no_recommendation_match", **out_base}

        return {"allocated": False, "reason": "no_llm_available",
                "provider_ref": None, "model_ref": None, "source": None, **out_base}


def allocate_llm(resources: Dict[str, Any]) -> Dict[str, Any]:
    """Fonction utilitaire stateless."""
    return Organisateur().allocate(resources)
