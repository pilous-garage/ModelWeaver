"""Tests d'intégration avancés : Workflow DSL & Succession.

Vérifie :
  1. Exécution d'un workflow chaîné (LLM -> Switch -> Sleep)
  2. Sauvegarde et restauration de l'état (state_json)
  3. Mécanisme de succession (transfert de session et terminaison)
"""

import json
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from modules.sql.db import ModelWeaverDB
from agents.factory import AgentFactory
from agents.worker import Worker
from agents.ticker import AsyncTicker

def run_test():
    print("\n🧪 Test Workflow DSL & Succession\n")
    
    with tempfile.NamedTemporaryFile(suffix=".db") as tmp:
        db = ModelWeaverDB(Path(tmp.name))
        factory = AgentFactory(db=db)
        
        # 1. Setup Provider
        pid = db.model_providers.save({
            "name": "Test Provider",
            "engine_type": "litellm",
            "model_name": "test-model",
            "endpoint_url": "http://localhost:8000",
        })

        # 2. Définir un workflow complexe
        # Sequence: LLM (S'appelle 'start') -> Switch -> Sleep (si OUI) -> LLM (S'appelle 'end')
        workflow = {
            "version": "1.0",
            "steps": [
                {
                    "id": "start",
                    "type": "llm_call",
                    "agent_id": "current",
                    "skill_prompt": "Réponds 'OUI' ou 'NON'.",
                    "output_capture": "choice",
                    "next": "switch_choice"
                },
                {
                    "id": "switch_choice",
                    "type": "switch",
                    "variable": "choice",
                    "conditions": [
                        {"operator": "CONTAINS", "value": "OUI", "next": "do_sleep"}
                    ],
                    "default": "finish"
                },
                {
                    "id": "do_sleep",
                    "type": "sleep",
                    "duration_seconds": 1,
                    "next": "final_call"
                },
                {
                    "id": "final_call",
                    "type": "llm_call",
                    "agent_id": "current",
                    "skill_prompt": "On se réveille !",
                    "next": "finish"
                },
                {
                    "id": "finish",
                    "type": "end",
                    "status": "SUCCESS"
                }
            ]
        }

        # Créer l'agent avec ce workflow
        agent = factory.create_agent(
            name="WorkflowAgent",
            role_type="assistant",
            provider_id=pid,
            config={"pipeline": workflow, "system_prompt": "Tu es un testeur."}
        )

        # Mock du LLM pour contrôler le flux
        # On veut simuler : 1. "OUI" -> 2. "Réveil réussi"
        call_count = 0
        def mock_llm(messages, variables, skill_prompt, temperature, max_tokens):
            nonlocal call_count
            call_count += 1
            if call_count == 1: return "OUI"
            return "Réveil réussi"

        factory._worker.llm_callback = mock_llm # Inject mock

        # --- PHASE 1 : Lancement et Sleep ---
        print("   Phase 1 : Lancement et passage au Sleep...")
        result = agent.execute(request="Démarre le workflow")
        
        if result["status"] != "sleeping":
            print(f"   ❌ Erreur inattendue: {result}")
        
        assert result["status"] == "sleeping", f"L'agent devrait dormir, got {result['status']}"
        assert result["next_step_id"] == "final_call", f"Étape suivante erronée: {result['next_step_id']}"
        print("   ✅ Passage au sleep OK")

        # --- PHASE 2 : Réveil et Fin ---
        print("   Phase 2 : Réveil et terminaison...")
        # On force le temps pour que la tâche soit mûre
        db.conn.execute("UPDATE wakeup_calls SET execute_after = '2000-01-01' WHERE status = 'TODO'")
        db.commit()
        
        tasks = db.wakeup_calls.list_pending()
        assert len(tasks) > 0, "Aucune tâche en attente"
        task_id = tasks[0]["task_id"]

        # Le worker exécute la suite
        result = factory._worker.execute(task_id)
        assert result["status"] == "success", f"Workflow non terminé avec succès: {result['status']}"
        assert call_count == 2, "Le LLM aurait dû être appelé 2 fois"
        print("   ✅ Réveil et fin de workflow OK")

        # --- PHASE 3 : Succession ---
        print("\n   Phase 3 : Test de la succession...")
        # On crée un second agent pour être le successeur
        successor = factory.create_agent(
            name="SuccesseurAgent",
            role_type="assistant",
            provider_id=pid,
        )

        # L'agent 1 se termine et passe le relais
        agent.exit(successor_role="assistant") 
        # On force le lien pour le test
        db.agents.set_successor(agent.agent_id, successor.agent_id)
        db.commit()

        # Vérifier le transfert de sessions
        sessions_before = db.sessions.list_all(agent_id=successor.agent_id)
        # On crée une session pour l'agent 1
        sid = db.sessions.create(agent.agent_id, "Session à transférer")
        db.agent_messages.add(sid, "user", "Hello")
        db.commit()

        # On effectue le transfert
        db.sessions.transfer_to_agent(agent.agent_id, successor.agent_id)
        db.commit()

        sessions_after = db.sessions.list_all(agent_id=successor.agent_id)
        assert len(sessions_after) > len(sessions_before), "Le transfert de session a échoué"
        print("   ✅ Transfert de session OK")

        db.close()
    print("\n✅ Tous les tests Workflow & Succession PASSÉS")

if __name__ == "__main__":
    run_test()
