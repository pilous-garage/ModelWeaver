"""Contrat PUBLIC du service `project_supervisor` : orchestration de projets multi-agents.

KIND = "supervisor"
NAME = "project_supervisor"

Dépendances :
  - AgentManager (list_active, tick, kill, get_by_name)
  - sqlite3 (projects.db)
  - services._common (mw_home)

Fournit :
  - add_task / assign_task / complete_task — file de tâches projet
  - set_workflow / advance_step — orchestration d'étapes ordonnées
  - set_budget / record_usage / get_budget — budget partagé projet
  - create_project / get_project_status / stop_project — cycle de vie projet
  - tick() — cycle de supervision qui distribue les tâches prêtes
"""

KIND = "supervisor"
NAME = "project_supervisor"
ENTRYPOINT = "supervisor.py"
RUNS = "ProjectSupervisor"

DEPENDS = [
    "services.agent_manager.service:AgentManager",
]