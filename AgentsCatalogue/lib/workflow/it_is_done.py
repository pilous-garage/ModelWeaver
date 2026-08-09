"""it_is_done — marqueur de fin de boucle appelé par le LLM.

Le LLM l'appelle quand il a fini de coder. Le FSM lit `it_is_done=True`
dans le step check_done pour sortir de la boucle de travail et passer à
la vérification git. Aucune autre action.
"""


def exec(inputs: dict, home: str) -> dict:
    return {"ok": True, "it_is_done": True,
            "summary": inputs.get("summary", "")}


__skills__ = ["exec"]
