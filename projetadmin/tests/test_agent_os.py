"""Tests de validation du module Agent OS.

Vérifie :
  1. Création des tables agent en BDD
  2. Création d'un model_provider
  3. Création d'un agent via la Factory
  4. Exécution d'une requête (execute)
  5. Cycle de vie complet (exit)
  6. Anti-fantôme du Ticker
"""

import json
import sqlite3
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.sql.db import ModelWeaverDB
from modules.sql.agent_repository import (
    AgentRepository, AgentMessageRepository,
    ModelProviderRepository, SessionRepository, WakeupCallRepository,
)
from AgentFrameWork.factory import AgentFactory
from AgentsCatalogue.role_manager import RoleManager
from AgentFrameWork.ticker import AsyncTicker


def test_schema_creation():
    """Vérifie que les tables agent sont créées."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))
        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_providers'"
        )
        assert cur.fetchone() is not None, "Table model_providers manquante"

        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agents'"
        )
        assert cur.fetchone() is not None, "Table agents manquante"

        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sessions'"
        )
        assert cur.fetchone() is not None, "Table sessions manquante"

        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='agent_messages'"
        )
        assert cur.fetchone() is not None, "Table agent_messages manquante"

        cur = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='wakeup_calls'"
        )
        assert cur.fetchone() is not None, "Table wakeup_calls manquante"

        db.close()
    print("  ✅ Schema creation OK")


def test_provider_and_agent_creation():
    """Crée un provider puis un agent via la Factory."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))
        factory = AgentFactory(db=db)

        pid = db.model_providers.save({
            "name": "Test Groq",
            "engine_type": "litellm",
            "model_name": "llama-3.3-70b-versatile",
            "endpoint_url": "https://api.groq.com/openai/v1",
            "max_concurrent": 5,
        })

        agent = factory.create_agent(
            name="TestAssistant",
            role_type="assistant",
            provider_id=pid,
            config={"temperature": 0.5},
        )

        assert agent.agent_id > 0
        assert agent.name == "TestAssistant"
        assert agent.role_type == "assistant"
        assert agent.status == "IDLE"

        loaded = factory.get_agent(agent_id=agent.agent_id)
        assert loaded is not None
        assert loaded.name == "TestAssistant"

        agents = factory.list_agents()
        assert len(agents) >= 1

        db.close()
    print("  ✅ Provider + Agent creation OK")


def test_execute_cycle():
    """Crée un agent, exécute une requête, vérifie le cycle complet.

    Note: sans endpoint_url, le Worker retourne 'no_endpoint' statut.
    Le test valide le cycle BDD (wakeup_call, messages, exit) sans appel HTTP.
    """
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))
        factory = AgentFactory(db=db)

        pid = db.model_providers.save({
            "name": "Test Provider",
            "engine_type": "litellm",
            "model_name": "test-model",
        })

        agent = factory.create_agent(
            name="ExecTest",
            role_type="assistant",
            provider_id=pid,
        )

        result = agent.execute(
            request="Dis bonjour",
            skill="chat",
        )

        assert result["status"] not in ("error",), f"Execute failed: {result.get('message')}"
        assert result["status"] == "no_endpoint"

        history = agent.get_history()
        assert len(history) >= 2

        agent.exit()
        assert agent.status == "STOPPED"

        db.close()
    print("  ✅ Execute cycle OK")


def test_wakeup_call_lifecycle():
    """Teste le cycle de vie d'une wakeup_call."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))

        pid = db.model_providers.save({
            "name": "Test", "engine_type": "litellm",
            "model_name": "test-model",
        })
        aid = db.agents.save({
            "name": "WakeTest", "role_type": "assistant",
            "provider_id": pid,
        })
        sid = db.sessions.create(aid)

        task_id = db.wakeup_calls.create(
            agent_id=aid, session_id=sid,
            skill="chat", request_payload='{"prompt":"hello"}',
        )
        assert task_id > 0

        pending = db.wakeup_calls.list_pending(limit=10)
        assert len(pending) >= 1

        claimed = db.wakeup_calls.claim(task_id)
        assert claimed

        task = db.wakeup_calls.get(task_id)
        assert task["status"] == "BUSY"

        db.wakeup_calls.complete(task_id, "OK")
        task = db.wakeup_calls.get(task_id)
        assert task["status"] == "COMPLETED"

        db.close()
    print("  ✅ Wakeup call lifecycle OK")


def test_anti_ghost():
    """Vérifie que le Ticker réinitialise les tâches BUSY au démarrage."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))

        pid = db.model_providers.save({
            "name": "Test", "engine_type": "litellm",
            "model_name": "test",
        })
        aid = db.agents.save({
            "name": "GhostTest", "role_type": "assistant",
            "provider_id": pid,
        })
        sid = db.sessions.create(aid)

        task_id = db.wakeup_calls.create(
            agent_id=aid, session_id=sid,
            skill="chat", request_payload='{}',
        )
        db.wakeup_calls.claim(task_id)

        task = db.wakeup_calls.get(task_id)
        assert task["status"] == "BUSY"

        count = db.wakeup_calls.reset_busy()
        assert count >= 1

        task = db.wakeup_calls.get(task_id)
        assert task["status"] == "TODO"

        db.close()
    print("  ✅ Anti-ghost mechanism OK")


def test_role_manager():
    """Vérifie le chargement des rôles."""
    rm = RoleManager()
    roles = rm.list_roles()
    assert "assistant" in roles, "Rôle assistant manquant"

    role = rm.get_role("assistant")
    assert role is not None
    assert "Tu es un assistant" in role.system_prompt
    assert "chat" in role.skills
    assert role.default_config["temperature"] == 0.7

    print(f"  ✅ Role manager OK ({len(roles)} rôle(s))")


def test_factory_request_agent():
    """Crée un agent jetable."""
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))
        factory = AgentFactory(db=db)

        pid = db.model_providers.save({
            "name": "Test", "engine_type": "litellm",
            "model_name": "test-model",
        })

        result = factory.create_request_agent(
            name="OneShot",
            role_type="assistant",
            request="Test rapide",
            provider_id=pid,
        )

        assert result is not None

        agent = factory.get_agent(name="OneShot")
        assert agent.status == "STOPPED"

        db.close()
    print("  ✅ Request agent (one-shot) OK")


if __name__ == "__main__":
    print("\n🧪 Agent OS — Tests\n")
    test_schema_creation()
    test_provider_and_agent_creation()
    test_execute_cycle()
    test_wakeup_call_lifecycle()
    test_anti_ghost()
    test_role_manager()
    test_factory_request_agent()
    print("\n✅ Tous les tests passés")
