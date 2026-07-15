"""Contrat des DEPENDANCES du service `api` : fonctions/classes externes consommées.

Vérifié par hardcheck : chaque symbole doit exister dans l'unité source, et le
service ne doit pas consommer d'autre symbole externe non déclaré ici.
"""
from services.installer_worker.jobs import (
    enqueue_job, job_status, list_jobs, cancel_job, clear_jobs,
    install_tool, uninstall_tool,
)
from modules.sql.db import ModelWeaverDB, CatalogueDB
from modules.checker.checker import Checker
from modules.key_manager.key_manager import KeyManager, KeyLockedError
from modules.key_manager.onboarder import Onboarder
from modules.llm_manager.llm_manager import (
    LLMManager, seed_providers, seed_models, seed_provider_models,
)
from modules.llm_manager.litellm_bridge import LiteLLMBridge
from modules.llm_manager.base_bridge import BridgeError, ErrorCategory

CONSUMES = {
    # File d'installation + install/uninstall réels.
    "services.installer_worker.jobs": [
        "enqueue_job", "job_status", "list_jobs", "cancel_job", "clear_jobs",
        "install_tool", "uninstall_tool",
    ],
    # Sous-modules consommés directement par le daemon.
    "services.installer_worker": ["jobs"],
    "services.watch_sysstate": ["service"],
    # Data-layer.
    "modules.sql.db": ["ModelWeaverDB", "CatalogueDB"],
    # Inspection système.
    "modules.checker.checker": ["Checker"],
    # Gestionnaire de clés (stockage keyring + verrou).
    "modules.key_manager.key_manager": ["KeyManager", "KeyLockedError"],
    "modules.key_manager.onboarder": ["Onboarder"],
    # Catalogue LLM (providers / modèles / recommandation).
    "modules.llm_manager.llm_manager": [
        "LLMManager", "seed_providers", "seed_models", "seed_provider_models",
    ],
    # LLM Bridge (chat, capacités, contexte).
    "modules.llm_manager.litellm_bridge": ["LiteLLMBridge"],
    "modules.llm_manager.base_bridge": ["BridgeError", "ErrorCategory"],
    "modules.llm_manager.local_engines": ["get_local_engine_manager", "LocalEngineManager"],
    # Helpers partagés.
    "services._common": ["_db_paths", "_quiet_stdout", "log_to_file", "acquire_instance_lock"],
    # Vérification des dépendances.
    "services.depends": ["check_all_units"],
    # Agent Manager (nouveau).
    "modules.sql.db": ["AgentsDB"],
    "services.agent_manager.service": ["AgentManager", "Agent"],
    "AgentFrameWork.fsm_interpreter": ["FSMInterpreter"],
}
