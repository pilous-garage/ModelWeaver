# Checklist — Refonte local_catalogue + buffer + script models.dev (2026-08-17)

Spec : `docs/local_catalogue_spec.md` · Idées : carnet section R.

## 1. local_catalogue (schéma + repo + routes)
- [x] Schéma SQL v4 : tables fixes `global_local_*` (data_type, source +
      mirroir_sources, shared_default, namespace, path, meta) +
      `{type}_data/_tag/_tag_type/_source_and_sharing` — reset v4
- [x] Modèle versionné multi-sources : UNIQUE(namespace, name, source_id,
      version), data_id = hash du quadruplé (une entrée par version/source)
- [x] Accès par sélection : tag (prioritaire) → préférence source
      (user>enterprise>official) → version (newest/oldest/littérale) ;
      syntaxe catalogue.<type>.<ns>...<name>[:<source>@<version>][:tag(nom)]
- [x] Gardes de source : add/modify/delete ne touchent que les lignes de la
      source de l'écriture ; modify d'une data étrangère = NOUVELLE entrée
      (source + version propre) ; jamais d'écrasement
- [ ] Repo `catalogue_local.py` (repo daemon) : porte sur la refonte sqlite —
      handler catalogue_local.py encore sur modules.sql (V2)
- [ ] file_watcher.py adapté (data_type fichier → `fichier_data`)
- [ ] catalogue_runtime.py (résolveur catalogue.a.b.fn) → sélection
      source@version : le résolveur doit utiliser les sélecteurs V4
- [ ] auth.py / openai_compat.py : requêtes mode=ro adaptées (renommage)
- [ ] prune (nettoyage vieilles versions par source, keep=N) : TODO

## 2. security
- [ ] privileges + conditions + security_supervisor → `global_local_*`
      (famille local_security, writer privé `write_catalogue_priv`)
- [ ] Re-seed des privilèges par défaut après reset (catalogue_privileges_defaults)

## 3. buffer (domaine SÉPARÉ — buffer.db, writer dédié `write_buffer`)
- [x] `buffer_op` (in/out, payload, status applied/error, external_tag) — dépôt
      pending par les importeurs (token write_buffer UNIQUEMENT, jamais le
      catalogue)
- [x] Consumer IN dans le domaine LOCAL (`local/write.consume_buffer`) : le
      writer local applique dans x_data en mini-batch non bloquant, marque +
      purge via les fonctions dédiées du buffer (autorisation spéciale) ;
      retry (status → pending)
- [ ] Consumer OUT (send) séparé : modifs locales → ops direction='out'
      (export distant futur) — TODO
- [ ] (Mise à plat ultérieure) registre `domain_writers` : familles local_data,
      local_security, local_env, local_support + buffer déclarées dans catalogue.db
- [ ] `catalogue_verif.py` adapté aux nouveaux noms de tables

## 3b. genere (alignement modèle multi-sources — À VALIDER par l'utilisateur)
- [ ] gen_data : UNIQUE(namespace, name_data, source_id, version) + table
      gen_source (même modèle que local v4) — requiert la validation du
      schéma genere par l'utilisateur (domaine pas encore validé)

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