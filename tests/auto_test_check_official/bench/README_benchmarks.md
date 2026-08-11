"""Guide des BENCHMARKS FSM (bench PC for FSM).

Conçu maintenant, EXÉCUTÉ plus tard (markers). Ce n'est PAS un seul test :
c'est une SUITE de benchmarks séparés, chacun mesurant la CAPACITÉ du moteur
sous une charge précise, avec suivi des RESSOURCES.

═══════════════════════════════════════════════════════════════════════════
OBJECTIF
═══════════════════════════════════════════════════════════════════════════
Déterminer jusqu'à COMBIEN d'agents très actifs / moyennement actifs /
peu actifs / greedy peuvent tourner en même temps, à quel rythme, et avec
quelles ressources (CPU / RAM / fds / taille BDD). C'est un benchmark de la
machine, pas un test de correction.

Les agents sont SIMPLES, SANS LLM : petites commandes, logique, appels shell
via le mini-shell, catalogue_local, team.chatroom, système d'autorisation
complet. Chaque benchmark exerce UN axe.

═══════════════════════════════════════════════════════════════════════════
AXES / NIVELLES D'ACTIVITÉ
═══════════════════════════════════════════════════════════════════════════
| niveau   | comportement            | objectif mesuré                  |
|----------|-------------------------|----------------------------------|
| très     | agents SANS sleep       | combien d'agents tournent en     |
| actif    | (boucle ininterrompue)  | boucle serrée sans s'effondrer   |
| moyen    | sleep 30s → 1h          | combien d'agents « pendants »    |
| actif    |                         | (hydratés mais en attente)       |
| peu      | sleep ~15 min           | capacité de dormeurs /           |
| actif    |                         | résurrection (wait_for)          |
| greedy   | occupation=continue     | le greedy pioche/travaille/      |
|          | (wait_for + waker)      | se rendort correctement          |

Chaque benchmark retourne un RAPPORT : {n_agents_max, rythme, cpu%, ram_mb,
fds, db_size_mb, durée}.

═══════════════════════════════════════════════════════════════════════════
STRUCTURE (tests/auto_test_check_official/bench/)
═══════════════════════════════════════════════════════════════════════════
Chaque fichier = UN benchmark ciblé (marker `bench`). Exécution :
    python3 -m pytest tests/auto_test_check_official/bench/ -m bench -q -s

  01_agents_very_active.py    — N agents sans sleep, mini-FSM + skills simples
  02_agents_medium_active.py  — N agents sleep 30s..1h (faux sleep court)
  03_agents_low_active.py     — N agents sleep 15 min (faux sleep court)
  04_agents_greedy.py         — greedy : waker, wait_for, pioche de tâches
  05_shell_minishell.py       — commandes shell via le mini-shell (batch)
  06_catalogue_io.py          — catalogue_local : upsert/list/search sous charge
  07_chatroom.py              — team.chatroom.send/read sous charge
  08_auth_system.py           — privileges : create/check/use/approve sous charge
  09_batch_writer.py          — batchs (2 files, garde-fous) sous charge
  10_mixed_30min.py           — STRESS GROUPÉ ~30 min (marker `stress30`)
  resources.py                — helper : moniteur CPU/RAM/fds/db (thread)

NOTE : fichiers préfixés `test_0X_` (pytest collecte `test_*.py`).

═══════════════════════════════════════════════════════════════════════════
MÉTHODE COMMUNE
═══════════════════════════════════════════════════════════════════════════
1. ESPACE RÉSERVÉ : toutes les données créées vivent dans
   auto-test-check-official/bench/* (cleanup par préfixe à la fin).
2. MONITEUR : thread qui échantillonne {cpu%, ram_mb, fds} toutes les 2s
   (resources.py). Rapport {min/max/moy}.
3. MONTÉE EN CHARGE : on ajoute des agents par paliers (ex. +10 toutes les
   5s) jusqu'à un seuil (timeout d'une op, ralentissement > X, erreur).
   n_agents_max = dernier palier réussi.
4. SANS LLM : bridge factice qui échoue si sollicité (les benchmarks sont
   moteur/catalogue, pas LLM).
5. NO LIMIT : les benchmarks n'imposent PAS de limite stricte — ils MESURENT.
   Les assertions ne sont que des garde-fous grossiers (ex. pas de crash,
   n_agents_max >= 10).

═══════════════════════════════════════════════════════════════════════════
ÉTAPE PRÉALABLE : TESTS FONCTIONNELS PAR AXE
═══════════════════════════════════════════════════════════════════════════
AVANT de lancer un benchmark, chaque axe doit avoir son TEST FONCTIONNEL
(court, < 5s) qui vérifie que la brique MARCHE. Les benchmarks supposent que
ces tests passent déjà :
  - FSM/chatroom/skills/inline  → tests/auto_test_check_official/ (existe)
  - auth/privileges/ask         → tests/test_minimal.py (existe)
  - catalogue_local/path/batch  → tests/test_minimal.py (existe)
  - mini-shell                  → à vérifier (AgentsCatalogue/lib/shell/tests)
  - greedy/wait_for             → à vérifier (tests/e2e_*)

═══════════════════════════════════════════════════════════════════════════
ÉTAT : conception validée, fichiers de benchmark À ÉCRIRE.
"""
