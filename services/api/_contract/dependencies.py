"""Contrat des DEPENDANCES du service `api` : fonctions externes consommées.

Vérifié par hardcheck : chaque symbole doit exister dans l'unité source, et le
service ne doit pas consommer d'autre symbole externe non déclaré ici.
"""

CONSUMES = {
    # Logique riche (héritée) — vouée à être découpée en modules/services.
    "gui_helper": [
        # opérations métier
        "check_python_deps",
        "get_system_state",
        "save_system_state",
        "init_databases",
        "check_databases",
        "get_catalogue_tools",
        "seed_catalogue",
        "sync_catalogue_remote",
        "update_tools_table",
        "get_installed_tools",
        "install_tool",
        "uninstall_tool",
        # file de jobs / utilitaires
        "ensure_install_jobs",
        "_enqueue_job",
        "_job_status",
        "_db_paths",
        "log_to_file",
    ],
}
