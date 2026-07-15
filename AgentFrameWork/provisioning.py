"""ProvisioningService — Logique de sélection et création d'agents.

Ce service décide quel modèle et quel provider utiliser pour un rôle donné.
Actuellement implémenté via un mapping statique, mais conçu pour être
remplacé par un agent LLM "Architecte".
"""

import time
from typing import Any, Dict, Optional
from sql.db import ModelWeaverDB
from AgentFrameWork.factory import AgentFactory

class ProvisioningService:
    """Gère la création d'agents optimaux pour un rôle donné."""

    def __init__(self, db: ModelWeaverDB, factory: AgentFactory):
        self.db = db
        self.factory = factory

    def request_agent(self, role_type: str, context: str = "general") -> Optional[int]:
        """
        Analyse le besoin (rôle + contexte) et crée l'agent le plus adapté.
        Retourne l'ID de l'agent créé.
        """
        # 1. Mapping simple Rôle $\to$ Provider/Modèle
        mapping = {
            "codeur": {
                "provider_ref": "groq", 
                "model_name": "llama-3.3-70b-versatile"
            },
            "architecte": {
                "provider_ref": "google", 
                "model_name": "gemini-2.0-flash"
            },
            "chercheur": {
                "provider_ref": "google", 
                "model_name": "gemini-2.0-flash"
            },
            "assistant": {
                "provider_ref": "groq", 
                "model_name": "llama-3.1-8b-instant"
            }
        }

        config_target = mapping.get(role_type, {
            "provider_ref": "groq", 
            "model_name": "llama-3.1-8b-instant"
        })

        provider = self.db.providers.get(config_target["provider_ref"])
        if not provider:
            provider = self.db.providers.list_all()[0] if self.db.providers.list_all() else None

        if not provider:
            return None

        agent_name = f"{role_type}_{int(time.time())}"
        agent = self.factory.create_agent(
            name=agent_name,
            role_type=role_type,
            provider_id=provider["id"],
            config={"context": context}
        )
        
        return agent.agent_id
