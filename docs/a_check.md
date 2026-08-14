# a_check — checklist de vérification (taskflow V0.16)

Vérifications à faire en réel, une à une. Coche quand validé.

## Sous-agents
- [x] **Le sous-agent `explore` prend les sub_tasks `exploration`** :
      quand le découpeur fait `ask_intel`, la sub_task `exploration` est créée
      (même workspace), attribuée par le supervisor au sous-agent
      `team:llm-code/decoupeur/explore`, prise en `doing`, et l'analysis
      (en waiting de l'exploration) est libérée quand l'exploration est done.
      Le sous-agent travaille dans le HOME du découpeur.
      → validé (A-CHECK : entry → découpeur → ask_intel → explore prend →
      exploration done → analysis libérée).
- [ ] Le sous-agent explore a `agents.home` = le home de son maître (découpeur).
- [ ] L'exploration done → l'analysis redevient unattributed (reprise par le
      découpeur avec le rapport d'exploration).

## Petites étapes (taskflow)
- [ ] `decoupe` à index relatifs : 0 = tâche courante, 1..n = nouvelles, avec
      merges intermédiaires + merge final.
- [ ] `ask_intel` anti-redondance (already_waiting + redundant_count).
- [ ] Le supervisor attribue/réveille sur les sub_tasks unattributed (amorce).
- [ ] Un coding done/ok → le supervisor crée la suite (review/testing/merge) ;
      un groupe complet → supervised ; une entrée close → respond.

## Scoring (V0.16)
- [ ] `model_call_log` : model_id résolu (provider_models) pour les appels récents.
- [ ] Le batcheur (5 min) remplit les buckets stables → score_batch (succès).
- [ ] `list_responded_models` / route `usage/responded_models` : tableau correct.
- [ ] `reset_buckets` : reconstruction depuis log + archive.
