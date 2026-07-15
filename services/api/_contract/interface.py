"""Contrat PUBLIC du service `api` : ce qui est accessible depuis l'extérieur.

Source de vérité du contrat multi-langage (GUI TS / CLI Python / Rust parlent
tous ces routes). Vérifié par hardcheck contre la table ROUTES réelle du daemon.
"""

KIND = "service"
NAME = "api"

# Table de routes réellement servie, désignée par "module:attribut".
ROUTES_SOURCE = "daemon:ROUTES + STREAMING_ROUTES"

# Point d'entrée du service (supervisé). Un seul daemon à la fois (lock api).
RUNS = "serve"

# Le daemon n'exige que Python + SQLite ; psutil est optionnel (fallback).
DEPENDS = []

# Routes exposées -> paramètres attendus ([] si aucun ; params optionnels tolérés).
EXPOSES = {
    # A. Système & environnement
    "system/info": [],
    "system/deps/check": [],
    "system/state/get": [],
    "system/state/save": [],
    # B. Bases
    "db/init": [],
    "db/check": [],
    # C. Catalogue
    "catalogue/tools/list": [],
    "catalogue/seed": [],
    "catalogue/sync": ["url"],
    "catalogue/tools_table/update": [],
    # D. Outils installés (synchrone)
    "tools/installed/list": [],
    "tools/install": ["ref"],
    "tools/uninstall": ["ref"],
    "tools/install/all": [],
    # F. Dépendances (modules/services)
    "deps/check": [],
    # E. File de jobs (asynchrone)
    "jobs/add": ["ref", "job_type"],
    "jobs/list": [],
    "jobs/status": ["id"],
    "jobs/cancel": ["id"],
    "jobs/clear": [],
    # G. Key Manager
    "keys/set": ["provider_ref", "api_key", "api_base", "identity", "tag", "grade"],
    "keys/get": ["provider_ref", "identity"],
    "keys/list": [],
    "keys/delete": ["ref"],
    "keys/set_lock": ["ref", "locked"],
    "keys/onboard": ["env_path"],
    # H. Providers (catalogue)
    "providers/list": [],
    # I. LLM Manager
    "llm/models/list": ["provider_ref"],
    "llm/recommend": ["use_case", "technical_level"],
    # K. LLM Bridge
    "llm/chat": ["provider_ref", "model_ref", "messages"],
    "llm/chat/stream": ["provider_ref", "model_ref", "messages"],
    "llm/capabilities": ["provider_ref", "model_ref"],
    "llm/bridge/status": ["provider_ref"],
    "llm/context/probe": ["provider_ref", "model_ref"],
    "llm/context/history": ["provider_ref", "model_ref", "limit"],
    # K2. LLM locaux (moteurs détectés sur la machine)
    "llm/local/list": [],
    "llm/local/start": ["engine"],
    "llm/local/stop": ["engine"],
    "llm/local/models": ["engine"],
    # L. Auth / Infra
    "auth/info": [],
    # J. Logs
    "logs/read": [],
    "logs/write": ["level", "message"],
    # M. Agent Manager
    "agent/list": [],
    "agent/get": ["agent_id", "name"],
    "agent/create": ["name", "role", "occupation"],
    "agent/delete": ["agent_id", "name"],
    "agent/execute": ["agent_id", "name", "request", "provider_ref", "model_ref"],
    "agent/manager/status": [],
    "agent/resources/evaluate": ["agent_id", "name", "resources"],
    "agent/admit": ["agent_id", "name"],
    "agent/signal": ["agent_id", "name", "type", "payload"],
    "agent/signals": ["agent_id", "name", "status"],
    "agent/signal/ack": ["signal_id"],
    "agent/signal/complete": ["signal_id", "result"],
    "agent/stream": ["agent_id", "name", "seq"],
    "agent/spawn": ["name", "role", "request", "occupation", "resources", "config", "provider_ref", "model_ref", "keep_sleeping"],
    "agent/handoff": ["from_id", "from_name", "to_id", "to_name"],
    # N. Chat Service (V0.6.6)
    "chat/session/create": ["name", "provider_ref", "model_ref", "system_prompt", "allow_read_others"],
    "chat/session/list": [],
    "chat/session/get": ["name"],
    "chat/session/delete": ["name"],
    "chat/session/update": ["name", "system_prompt", "provider_ref", "model_ref", "allow_read_others"],
    "chat/session/send": ["name", "message", "provider_ref", "model_ref", "stream", "temperature", "max_tokens"],
    "chat/session/history": ["name"],
    "chat/session/read": ["name", "other"],
    "chat/session/stream": ["name", "seq"],
}
