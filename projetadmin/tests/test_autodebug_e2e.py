"""Test E2E de l'Auto-Debug Loop.
Scénario :
1. Création d'un fichier fibonacci.py avec un bug.
2. Injection d'une shared_task pour le fixer.
3. Lancement du Ticker.
4. Observation de la boucle : Codeur -> TestRunner -> Debugger -> Codeur.
5. Validation finale.
"""

import os
import time
import threading
import logging
import json
from modules.sql.db import ModelWeaverDB
from agents.ticker import AsyncTicker
from agents.factory import AgentFactory
from agents.provisioning import ProvisioningService

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger("autodebug_test")

# --- CONFIGURATION ---
BUGGY_CODE = """
def fibonacci(n):
    # BUG: Le cas de base pour n=1 est faux
    if n <= 0: return 0
    if n == 1: return 0 # Devrait être 1
    return fibonacci(n-1) + fibonacci(n-2)

if __name__ == "__main__":
    print(f"Fib(5) = {fibonacci(5)}") # Attendu: 5, Actuel: 3
"""

def setup_environment(db: ModelWeaverDB):
    logger.info("⚙️ Configuration de l'environnement...")
    
    # 1. Peupler les providers et api_keys (nécessaire pour Docker)
    if not db.providers.list_all():
        logger.info("Peuplement des providers de test...")
        
        # Lecture du .env pour les clés
        keys = {}
        try:
            with open(".env", "r") as f:
                for line in f:
                    if "=" in line and not line.startswith("#"):
                        k, v = line.strip().split("=", 1)
                        keys[k] = v
        except Exception as e:
            logger.warning("Impossible de lire le .env : %s", e)

        # Provider Groq
        p_groq = db.providers.save({
            "ref": "groq",
            "name": "Groq",
            "provider_type": "cloud",
            "api_type": "openai",
        })
        db.keys.save({
            "ref": "key_groq",
            "provider_id": p_groq,
            "key_value": keys.get("GROQ_API_KEY", ""),
            "tag": "paid"
        })

        # Provider Google
        p_google = db.providers.save({
            "ref": "google",
            "name": "Google",
            "provider_type": "cloud",
            "api_type": "gemini",
        })
        db.keys.save({
            "ref": "key_google",
            "provider_id": p_google,
            "key_value": keys.get("GOOGLE_GEMINI_API_KEY", ""),
            "tag": "paid"
        })
        
        # On crée des entrées dans model_providers
        db.conn.execute("""
            INSERT INTO model_providers (name, engine_type, model_name, endpoint_url, provider_id)
            VALUES ('Groq-Llama', 'openai_api', 'llama-3.3-70b-versatile', 'https://api.groq.com/openai/v1', ?)
        """, (p_groq,))
        db.conn.execute("""
            INSERT INTO model_providers (name, engine_type, model_name, endpoint_url, provider_id)
            VALUES ('Google-Gemini', 'openai_api', 'gemini-2.0-flash', 'https://generativelanguage.googleapis.com/v1beta/openai', ?)
        """, (p_google,))
        
        db.commit()

    # 2. Création du fichier buggé
    with open("fibonacci.py", "w") as f:
        f.write(BUGGY_CODE)
    
    # 3. Provisionnement des agents nécessaires
    factory = AgentFactory(db=db)
    provisioning = ProvisioningService(db, factory)
    
    provisioning.request_agent("codeur", "project:test")
    provisioning.request_agent("test_runner", "project:test")
    provisioning.request_agent("debugger", "project:test")
    provisioning.request_agent("critique", "project:test")
    
    db.commit()

def run_test():
    db = ModelWeaverDB()
    setup_environment(db)
    
    # 3. Injection de la tâche initiale
    task_id = db.shared_tasks.create(
        title="Fix Fibonacci Bug",
        description="Le fichier fibonacci.py contient une erreur dans le cas de base. Corrige-le pour que Fib(1)=1 et Fib(5)=5.\n\nPath: fibonacci.py",
        required_role="codeur",
        context="project:test",
        priority=10
    )
    logger.info("🚀 Tâche injectée #%d. Lancement du Ticker...", task_id)
    
    # 4. Lancement du Ticker dans un thread
    ticker = AsyncTicker(db=db)
    
    def ticker_loop():
        import asyncio
        asyncio.run(ticker.start())

    thread = threading.Thread(target=ticker_loop, daemon=True)
    thread.start()
    
    # 5. Monitoring de la boucle
    start_time = time.time()
    timeout = 600 # 10 minutes
    
    while time.time() - start_time < timeout:
        task = db.shared_tasks.get(task_id)
        status = task["status"]
        logger.info("Statut actuel de la tâche #%d : %s", task_id, status)
        
        if status == "DONE":
            logger.info("✅ Tâche marquée comme DONE !")
            break
        
        # On regarde si on a des tâches enfants (le debug en cours)
        children = db.conn.execute(
            "SELECT title, status FROM shared_tasks WHERE parent_task_id = ?", 
            (task_id,)
        ).fetchall()
        if children:
            for child in children:
                logger.info("  └─ Enfant: %s [%s]", child["title"], child["status"])
        
        time.sleep(10)
    
    # 6. Validation finale
    try:
        with open("fibonacci.py", "r") as f:
            content = f.read()
        if "if n == 1: return 1" in content or "return 1" in content:
            logger.info("🎉 SUCCÈS : Le code a été corrigé !")
            return True
        else:
            logger.error("❌ ÉCHEC : Le code n'a pas été corrigé correctement.")
            logger.info("Contenu final :\n%s", content)
            return False
    except Exception as e:
        logger.error("Erreur lors de la lecture du fichier final : %s", e)
        return False

if __name__ == "__main__":
    try:
        success = run_test()
        exit(0 if success else 1)
    except Exception as e:
        logger.exception("Erreur fatale durant le test")
        exit(1)
