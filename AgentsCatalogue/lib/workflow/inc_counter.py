"""inc_counter — incrémente un compteur de boucle (anti-boucle reviewer)."""


def exec(inputs: dict, home: str) -> dict:
    raw = str(inputs.get("value", "") or "0").strip()
    try:
        cur = int(raw)
    except (ValueError, TypeError):
        cur = 0
    return {"ok": True, "value": cur + 1}


__skills__ = ["exec"]
