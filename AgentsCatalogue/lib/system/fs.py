"""Accès hôte (chemins absolus, gated par FsAuthManager).

Migrés depuis services/skill_manager.py (_exec_host_*). Le helper
_agent_id_from_ws est reproduit à l'identique.
"""

import os
from pathlib import Path

from services.fs_auth import FsAuthManager, FsAuthError
from services.sandbox import Sandbox, SandboxError


def _agent_id_from_ws(ws: str, inputs: dict) -> str:
    aid = inputs.get("agent_id", "")
    if aid:
        return str(aid)
    parts = Path(ws).parts
    if "memagent" in parts:
        return str(parts[parts.index("memagent") + 1])
    return ""


def host_read(inputs: dict, ws: str) -> dict:
    agent_id = _agent_id_from_ws(ws, inputs)
    path = inputs.get("path", "")
    try:
        mgr = FsAuthManager()
        if not mgr.check(int(agent_id), path, want_write=False):
            raise FsAuthError(f"accès refusé: {path}")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        mgr.close()
        return {"content": content}
    except FsAuthError as e:
        return {"content": "", "error": str(e)}
    except Exception as e:
        return {"content": "", "error": str(e)}


def host_write(inputs: dict, ws: str) -> dict:
    agent_id = _agent_id_from_ws(ws, inputs)
    path = inputs.get("path", "")
    content = inputs.get("content", "")
    try:
        mgr = FsAuthManager()
        if not mgr.check(int(agent_id), path, want_write=True):
            raise FsAuthError(f"écriture refusée: {path}")
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        mgr.close()
        return {"ok": True}
    except FsAuthError as e:
        return {"ok": False, "error": str(e)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def host_run(inputs: dict, ws: str) -> dict:
    agent_id = _agent_id_from_ws(ws, inputs)
    command = inputs.get("command", "")
    cwd = inputs.get("cwd", "/")
    try:
        mgr = FsAuthManager()
        if not mgr.check(int(agent_id), cwd, want_write=True):
            raise FsAuthError(f"cwd non autorisé: {cwd}")
        mgr.close()
        stdout, stderr, rc = Sandbox().run(command, cwd=cwd, timeout=30)
        return {"stdout": stdout, "stderr": stderr, "exit_code": rc}
    except FsAuthError as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}
    except SandboxError as e:
        return {"stdout": "", "stderr": str(e), "exit_code": -1}


__skills__ = ["host_read", "host_write", "host_run"]
