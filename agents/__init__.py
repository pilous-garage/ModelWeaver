"""Agent OS — Module d'agents persistants stateless.

Architecture Phénix : les agents n'existent que comme lignes en BDD.
Un Ticker asynchrone les hydrate à la demande pour exécuter des tâches,
puis les déshydrate immédiatement après.
"""

from agents.factory import Agent, AgentFactory
from agents.ticker import AsyncTicker
from agents.role_manager import RoleManager
from agents.worker import Worker

__all__ = ["Agent", "AgentFactory", "AsyncTicker", "RoleManager", "Worker"]
