# Last Session — ModelWeaver

## Résumé de la session
- La commande `opencode` a été restaurée pour rester directe.
- Le bridge ModelWeaver est désormais installé sous `opencode-modelweaver`.
- Le wrapper route vers LiteLLM, enregistre la trace de route et tente les modèles de secours de façon visible.
- Les erreurs d’authentification, de saturation et de timeout déclenchent maintenant un rebond explicite.

## Fichiers clés
- [modelweaver.py](../modelweaver.py)
- [.modelweaver/route_trace.log](../.modelweaver/route_trace.log)
- [tests/test_opencode_litellm_bridge.py](../tests/test_opencode_litellm_bridge.py)
