"""Optimisation de contexte (compression de messages).

Migrée depuis services/skill_manager.py (_exec_optimize_context).
"""


def optimize(inputs: dict, ws: str) -> dict:
    messages = list(inputs.get("messages", []))
    max_chars = int(inputs.get("max_chars", 50000))
    budget = inputs.get("budget_remaining")

    total = sum(len(m.get("content", "")) for m in messages)
    ratio = 1.0
    if total <= max_chars:
        return {"messages": messages, "compression_ratio": 1.0}

    if budget is not None and budget < 1000:
        max_chars = max(max_chars // 4, 1000)

    kept = []
    chars = 0
    for m in reversed(messages):
        sz = len(m.get("content", "")) + 50
        if chars + sz > max_chars and kept:
            break
        kept.insert(0, m)
        chars += sz

    ratio = round(total / max(chars, 1), 2)
    return {"messages": kept, "compression_ratio": ratio}


__skills__ = ["optimize"]
