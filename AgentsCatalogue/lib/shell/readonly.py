"""shell.readonly — exécute UNIQUEMENT des commandes taggées read-only.

L'analyste/découpeur n'a PAS le droit d'écrire : on lui donne un mini-shell
restreint. Le tag `is_read_only` est porté par les commandes de
_READ_ONLY_CMDS. Toute commande non flaggée est REFUSÉE (ok=False, refused=True)
avec un message invitant à utiliser workspace/report_read_only@v1 pour la faire
examiner.
"""

from pathlib import Path

# Commandes dont la lecture seule est autorisée (le nom ET les flags doivent
# être read-only). git n'est autorisé qu'en lecture (status/log/diff/show...).
_READ_ONLY_CMDS = {
    "ls", "cat", "head", "tail", "grep", "find", "df", "du", "free",
    "stat", "file", "wc", "sort", "uniq", "pwd", "echo", "which",
    "whereis", "id", "uname", "env", "printenv", "date", "git",
}

# Sous-commandes git autorisées (lecture pure).
_GIT_READ_SUBS = {
    "status", "log", "diff", "show", "branch", "rev-parse", "rev-list",
    "ls-files", "ls-tree", "cat-file", "describe", "tag", "config",
}


def _command_token(cmd: str) -> list:
    """Tokenise grossièrement la commande (gère les quotes simples)."""
    import shlex
    try:
        return shlex.split(cmd)
    except Exception:
        return cmd.split()


def _is_read_only(cmd: str) -> bool:
    toks = _command_token(cmd)
    if not toks:
        return False
    base = toks[0]
    if base not in _READ_ONLY_CMDS:
        return False
    # git : vérifier la sous-commande (read-only uniquement).
    if base == "git":
        if len(toks) < 2:
            return False
        if toks[1].startswith("-"):
            # git -C ... : regarder la première sous-commande après les flags.
            i = 2
            while i < len(toks) and toks[i].startswith("-"):
                i += 1
            sub = toks[i] if i < len(toks) else ""
            return sub in _GIT_READ_SUBS
        return toks[1] in _GIT_READ_SUBS
    return True


def exec(inputs: dict, home: str) -> dict:
    cmd = (inputs.get("command") or "").strip()
    if not cmd:
        return {"stdout": "", "stderr": "command empty", "exit_code": 1,
                "ok": False, "refused": False, "reason": "commande vide"}
    if not _is_read_only(cmd):
        base = _command_token(cmd)[0]
        return {
            "stdout": "", "exit_code": 1, "ok": False, "refused": True,
            "reason": (f"commande non read-only refusée ({base}) — non flaggée "
                       "is_read_only. Utilise workspace/report_read_only@v1 "
                       "pour la soumettre au gestionnaire, mais essaie de faire "
                       "l'analyse sans elle."),
            "stderr": "read-only violation",
        }
    agent_id = Path(home).name
    try:
        from services.agent_shell_manager import agent_shell_manager
        agent_shell_manager.init()
        ag_sh = agent_shell_manager.get(agent_id)
        if ag_sh is None:
            ag_sh = agent_shell_manager.get_or_create(
                agent_id=agent_id, home_root=Path(home))
        result = ag_sh.run(cmd)
        return {
            "stdout": result.get("stdout", ""),
            "stderr": result.get("stderr", ""),
            "exit_code": result.get("exit_code", 1),
            "ok": result.get("exit_code", 1) == 0,
            "refused": False,
            "status": result.get("status", "ok"),
            "request_id": result.get("request_id"),
        }
    except Exception as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1,
                "ok": False, "refused": False, "reason": str(e)}


__skills__ = ["exec"]
