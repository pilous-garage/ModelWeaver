"""check_work_state — déduit l'état de travail du LLM depuis le output_capture.

Le output_capture d'un step llm_call est la réponse textuelle du LLM, qui
peut contenir la trace des tools appelés (it_is_done / exit_loop_too_hard /
making_progress). On cherche les marqueurs pour retourner l'état.
"""

import re


def exec(inputs: dict, home: str) -> dict:
    last = str(inputs.get("last_work", "") or "")
    if not last:
        return {"ok": True, "state": "continue"}
    # Trace des tools appelés (le FSM injecte la réponse tool dans les messages).
    if re.search(r"it_is_done|making_progress[^}]*done|\"making_progress\"\s*:\s*true", last):
        return {"ok": True, "state": "done"}
    if re.search(r"exit_loop_too_hard|too_hard", last):
        return {"ok": True, "state": "too_hard"}
    return {"ok": True, "state": "continue"}


__skills__ = ["exec"]
