"""making_progress — réponse du LLM au max_loop (making_progress | too_hard)."""


def exec(inputs: dict, home: str) -> dict:
    answer = (inputs.get("answer") or "").strip().lower()
    if answer == "too_hard":
        return {"ok": True, "making_progress": False, "too_hard": True,
                "reason": inputs.get("reason", "")}
    return {"ok": True, "making_progress": True, "too_hard": False,
            "reason": inputs.get("reason", "")}


__skills__ = ["exec"]
