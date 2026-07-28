"""LLM tool interface — wrapper pour que l'agent LLM utilise le shell.

Usage :
    from AgentsCatalogue.lib.shell.tool import execute_cmd
    result = execute_cmd(shell, "ls /workspace")
    # {"exit_code": 0, "stdout": "...", "stderr": "", "status": "success"}
"""

from typing import Any, Dict, Optional

from .shell import Shell


def execute_cmd(shell: Shell, cmd: str) -> Dict[str, Any]:
    """Exécute une commande shell et retourne un résultat structuré.

    C'est l'interface principale pour que l'agent LLM appelle le shell
    via un tool call.

    Retourne :
        {
            "exit_code": int,
            "stdout": str,
            "stderr": str,
            "status": "success" | "error",
            "error": str | None,  # seulement si erreur interne shell
        }
    """
    result = shell.run(cmd)
    return {
        "exit_code": result.get("exit_code", 1),
        "stdout": result.get("stdout", ""),
        "stderr": result.get("stderr", ""),
        "status": result.get("status", "error"),
        "error": result.get("error"),
    }


def execute_cmd_pending(shell: Shell, request_id: str) -> Optional[Dict[str, Any]]:
    """Vérifie l'état d'une demande d'autorisation en attente.

    Si la demande a été approuvée, retourne le résultat de la commande.
    Si refusée, retourne un message d'erreur.
    Si encore en attente, retourne None.
    """
    from .auth_request import request_handler

    req = request_handler.get(request_id)
    if req is None:
        return {"exit_code": 1, "stdout": "", "stderr": "demande introuvable", "status": "error"}
    if req.status.value == "approved":
        return {"exit_code": 0, "stdout": f"demande {request_id} approuvée par {req.approver_id}", "stderr": "", "status": "approved"}
    if req.status.value == "denied":
        return {"exit_code": 1, "stdout": "", "stderr": f"demande refusée par {req.approver_id}: {req.rejection_reason or 'pas de raison'}", "status": "denied"}
    return None  # encore en attente