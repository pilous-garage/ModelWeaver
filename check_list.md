# Checklist — Refonte local_catalogue + buffer + script models.dev (2026-08-17)

Spec : `docs/local_catalogue_spec.md` · Idées : carnet section R.

## 1. local_catalogue (schéma + repo + routes)
- [ ] Schéma SQL : tables fixes `global_local_*` (data_type, shared_default,
      namespace, path, meta) + `{type}_data/_tag/_tag_type/_source_and_sharing`
      (data_id = hash stable, data_value_type, last_modify colonne) +
      `global_local_buffer_op` — reset (pas de migration par renommage)
- [ ] Repo `catalogue_local.py` : create_type, data_id hash, upsert/delete/
      query par (data_type_id, data_id), row → colonnes dynamiques, tags
      typés (tag_value_type), partage, refresh (last_modify), writer mini-batch
- [ ] Handler `catalogue_local.py` : routes data_type/data/tag/tag_type/
      sharing (add/delete/modify/get/list/search + data_refresh) + buffer
      push/process/status — PAS de SQL par le daemon
- [ ] `file_watcher.py` adapté (data_type fichier → `fichier_data`)
- [ ] `catalogue_runtime.py` (résolveur catalogue.a.b.fn) → `{type}_data`
- [ ] `auth.py` / `openai_compat.py` : requêtes mode=ro adaptées (renommage)

## 2. security
- [ ] privileges + conditions + security_supervisor → `global_local_*`
      (famille local_security, writer privé `write_catalogue_priv`)
- [ ] Re-seed des privilèges par défaut après reset (catalogue_privileges_defaults)

## 3. buffer_catalogue
- [ ] `global_local_buffer_op` (in/out, payload, status applied/error,
      external_tag) + consumer du writer en mini-batch (non bloquant)
- [ ] Registre `domain_writers` : familles local_data, local_security,
      local_env, local_support, local_buffer déclarées dans catalogue.db
- [ ] `catalogue_verif.py` adapté aux nouveaux noms de tables

## 4. Scripts de remplissage
- [ ] Script models.dev → buffer (providers/modèles/endpoints en `row`,
      toutes les datas utiles)
- [ ] Script agents/skills/teams : 11 agents (chat-pilot, as-llm-leader,
      greedy-{coder,consensus,decoupeur,explore,merger,prepare-response,
      reviewer,tester}, proxy_llm_fallback) + 2 teams (dev-chat, llm-code)
      + 196 skills (import complet, filtre obsolètes plus tard)
- [ ] Vérification de bout en bout (import → buffer → consumer → lectures)

## 5. Mise à plat catalogue.db (tables dérivées)
- [ ] models / provider_models (+ mapping) / endpoints / providers /
      type_key (+ budgets) : dériver le catalogue LLM depuis local_catalogue
      (données models.dev)
- [ ] Vérification cohérence (catalogue_verif)

## Dépendances / fichiers supprimés
- équipe obsolètes (exemple-team, build, swarm-selfimprove(-v2),
  gui-tasks, bug-busters) : fichiers supprimés — `projetadmin/tests/
  test_team_swarm.sh` à corriger/retirer