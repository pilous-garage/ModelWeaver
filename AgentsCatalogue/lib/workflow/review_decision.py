"""review_decision — extrait la décision du reviewer depuis la trace des tools.

Le do_review expose UNIQUEMENT review_verdict_v1. La trace (review_out)
contient le tool_call avec ses arguments, ex. :
  review_verdict_v1({"decision": "disapprove", "detail": "..."})
On détecte la décision (approve | disapprove | ask_more_intel) depuis les
arguments OU le texte. Si AUCUN appel review_verdict n'a eu lieu (réponse
texte sans tool_call), retourne unknown → le FSM traite comme une erreur.
"""

import re


def exec(inputs: dict, home: str) -> dict:
    text = str(inputs.get("review_text") or inputs.get("review_out") or "")
    low = text.lower()

    # 1) Arguments du tool_call (format JSON dans la trace).
    m = re.search(r'"decision"\s*:\s*"([^"]+)"', low)
    if m:
        d = m.group(1).strip().lower()
        if d == "approve":
            return {"ok": True, "decision": "approved"}
        if d == "disapprove":
            return {"ok": True, "decision": "disapproved"}
        if d in ("ask_more_intel", "ask_more"):
            return {"ok": True, "decision": "more_intel"}

    # 2) Fallback : le tool a-t-il été appelé du tout ? (trace des noms)
    if "review_verdict" not in low and "review_verdict_v1" not in low:
        # Réponse texte SANS le tool review_verdict → pas de décision ferme.
        # On tolère un « approve/disapprove » explicite en texte.
        if re.search(r"(verdict\s*[:=]?\s*done|j[' ]?approuve|approve|conforme)", low):
            return {"ok": True, "decision": "approved"}
        if re.search(r"(disapprove|non conforme|à corriger|rejette|rejet|refus)", low):
            return {"ok": True, "decision": "disapproved"}
        if re.search(r"(plus d'?infos|plus de contexte|intel|besoin de voir)", low):
            return {"ok": True, "decision": "more_intel"}
        return {"ok": True, "decision": "unknown"}

    # 3) Le tool a été appelé mais sans arguments parsables → inconnu.
    return {"ok": True, "decision": "unknown"}


__skills__ = ["exec"]
