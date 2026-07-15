"""BaseBridge — Interface déclarative pour tout bridge LLM.

Contrat vérifié par hardcheck. N'importe quel bridge (LiteLLM, OpenAI direct,
custom, etc.) doit exposer cette surface. Les agents consomment BaseBridge,
pas l'implémentation concrète.
"""

import sys
import os
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List, Optional, Any, Iterator

sys.path.insert(0, os.path.dirname(__file__))


# ── Types partagés ─────────────────────────────────────────────

class ErrorCategory(Enum):
    """Classification des erreurs LLM pour traitement automatique."""
    AUTH = "auth"
    RATE_LIMIT = "rate_limit"
    CONTEXT = "context"
    SERVER = "server"
    TIMEOUT = "timeout"
    UNKNOWN = "unknown"


@dataclass
class ModelCapabilities:
    """Capacités réelles d'un modèle (fenêtre effective, pas celle annoncée)."""
    context_window: int
    max_output: int
    cost_input_per_1k: Optional[float] = None
    cost_output_per_1k: Optional[float] = None
    supports_vision: bool = False
    supports_function_calling: bool = False
    mode: str = "chat"


@dataclass
class ChatResponse:
    """Réponse unifiée d'un appel LLM, indépendamment du provider."""
    content: str
    model: str
    finish_reason: str = "stop"
    usage: Dict[str, int] = field(default_factory=dict)
    raw: Any = None
    budget: Dict[str, Any] = field(default_factory=dict)


@dataclass
class BridgeError(Exception):
    """Erreur classifiée remontée par le bridge quand un appel échoue.
    Hérite de Exception pour pouvoir être levée et attrapée."""
    category: ErrorCategory
    message: str
    provider_ref: str
    model_ref: str
    retry_after_seconds: Optional[float] = None
    detected_context_limit: Optional[int] = None
    raw: Any = None

    def __str__(self):
        return f"[{self.category.value}] {self.provider_ref}/{self.model_ref}: {self.message}"


# ── Interface déclarative ──────────────────────────────────────

class BaseBridge(ABC):
    """Contrat pour tout bridge LLM.

    KIND = "bridge"
    NAME = "base_bridge"
    MODULE = "modules.llm_manager.base_bridge"
    EXPORTS = ['BaseBridge', 'ModelCapabilities', 'ChatResponse',
               'ErrorCategory', 'BridgeError']
    """

    # ── Chat ───────────────────────────────────────────────────

    @abstractmethod
    def chat(self, provider_ref: str, model_ref: str,
             messages: List[Dict[str, str]],
             temperature: float = 0.7,
             max_tokens: Optional[int] = None,
             system_prompt: Optional[str] = None,
             stream: bool = False,
             **params) -> ChatResponse:
        """Envoie un prompt et retourne la réponse unifiée.

        Lève BridgeError si l'appel échoue (après classification de l'erreur).
        L'agent ne voit jamais l'erreur brute du provider.
        """
        ...

    @abstractmethod
    def chat_stream(self, provider_ref: str, model_ref: str,
                    messages: List[Dict[str, str]],
                    temperature: float = 0.7,
                    max_tokens: Optional[int] = None,
                    system_prompt: Optional[str] = None,
                    **params) -> Iterator[str]:
        """Streaming chunk par chunk. Chaque yield = un morceau de texte."""
        ...

    # ── Capacités ──────────────────────────────────────────────

    @abstractmethod
    def get_capabilities(self, provider_ref: str,
                         model_ref: str) -> ModelCapabilities:
        """Retourne les capacités réelles (fenêtre effective, coûts, features).

        Priorité :
        1. Mesure effective enregistrée (provider_models.context_window_effective)
        2. litellm.model_cost (ou équivalent)
        3. Fallback catalogue
        """
        ...

    # ── Découverte ─────────────────────────────────────────────

    @abstractmethod
    def list_available_providers(self) -> List[Dict[str, Any]]:
        """Providers servis par ce bridge (ceux avec clé + locaux sans clé)."""
        ...

    @abstractmethod
    def list_available_models(self,
                              provider_ref: str) -> List[Dict[str, Any]]:
        """Modèles disponibles pour un provider via ce bridge."""
        ...

    # ── Santé ─────────────────────────────────────────────────

    @abstractmethod
    def health_check(self,
                     provider_ref: Optional[str] = None) -> Dict[str, Any]:
        """Ping rapide. Sans provider_ref → vérifie la disponibilité générale."""
        ...

    # ── Classification ────────────────────────────────────────

    @abstractmethod
    def classify_error(self, error: Any,
                       provider_ref: str = "",
                       model_ref: str = "") -> BridgeError:
        """Analyse une exception brute et la classifie.

        Route prévue pour un futur module Analyseur d'erreur — le bridge
        expose déjà la classification, le module pourra l'enrichir plus tard.
        """
        ...