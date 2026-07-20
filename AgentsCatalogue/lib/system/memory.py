"""Mémoire persistante par agent (mw_home()/memagent/{id}/mem).

Migrée depuis services/skill_manager.py (_exec_memory_*). Le helper
_memory_root est reproduit à l'identique.
"""

import json
from pathlib import Path

from services._common import mw_home


def _memory_root(agent_id: str) -> Path:
    return mw_home() / "memagent" / str(agent_id) / "mem"


def memory_write(inputs: dict, ws: str) -> dict:
    agent_id = inputs.get("agent_id", "")
    namespace = inputs.get("namespace", "default")
    key = inputs.get("key", "")
    value = inputs.get("value")
    if not agent_id or not key:
        return {"ok": False, "path": "",
                "error": "agent_id et key requis"}
    safe_ns = "".join(c for c in namespace if c.isalnum() or c in "-_")
    safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")
    root = _memory_root(agent_id) / safe_ns
    root.mkdir(parents=True, exist_ok=True)
    fp = root / f"{safe_key}.json"
    fp.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                  encoding="utf-8")
    return {"ok": True, "path": str(fp)}


def memory_read(inputs: dict, ws: str) -> dict:
    agent_id = inputs.get("agent_id", "")
    namespace = inputs.get("namespace", "default")
    key = inputs.get("key", "")
    if not agent_id or not key:
        return {"found": False, "value": None,
                "error": "agent_id et key requis"}
    safe_ns = "".join(c for c in namespace if c.isalnum() or c in "-_")
    safe_key = "".join(c for c in key if c.isalnum() or c in "-_.")
    root = _memory_root(agent_id) / safe_ns
    fp = root / f"{safe_key}.json"
    if not fp.exists():
        return {"found": False, "value": None}
    try:
        value = json.loads(fp.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"found": False, "value": None, "error": "lecture échouée"}
    return {"found": True, "value": value}


__skills__ = ["memory_write", "memory_read"]
