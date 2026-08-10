"""agent_graph_utils — logique de graphe (FSM / Pétri), pure Python.

Ce module ne contient QUE de la logique mathématique de graphe :
  - yaml_to_fsm : agent YAML → graphe FSM hiérarchique (nœuds avec
    id/type/label/ref/tags/vars[inner], arêtes from→to).
  - (à venir) petri : transformation en réseau de Pétri, fold/unfold,
    visibilité, etc. — TOUTE la logique backend, sans thème ni positions.

Le GUI (v2) consomme ces fonctions via le service `utilities` (routes
`graphe_utile/*`) ; il ne s'occupe que du rendu (fold visuel, placement).
"""
