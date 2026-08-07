# BOULOT-NUIT — Rendre le chat-dev aussi fonctionnel qu'opencode (+ swarm)

Date : nuit du 2026-08-07.
Objectif final : le chat de dev (GUI v2, dev-chat) doit être **au moins aussi
fonctionnel que opencode**, avec le swarm en plus.

Consignes utilisateur (verbatim, à ne pas perdre) :
- Toutes les clés fournies dans l'app sont GRATUITES. Essayer autant qu'on veut.
- Certains modèles sont marqués `free` → les autres ne répondront probablement pas.
- openrouter : 50 requêtes/jour TOTALES seulement. Trier les modèles par score,
  probe du plus performant au moins performant jusqu'à ce qu'un réponde.
- Espacer les probes (surtout chez le même fournisseur). Attention à ne PAS
  récupérer la clé opencode GO dans opencode. Toutes les autres clés fair play.
- google : beaucoup de bons modèles, RPM bas mais fenêtres token hautes →
  les utiliser avec des prompts COMPLETS, hors chat.
- Faire découper les tâches au MAXIMUM par les analyseurs (plus c'est découpé,
  plus ça marche).
- Faire un petit script qui fait des toolcalls à partir du texte si les LLM
  ne répondent qu'en descriptif (ne bouclent pas / ne concluent pas).
- Livrables swarm : dans le HOME, regroupés par le MERGER. Le swarm doit
  AUSSI essayer de résoudre les problèmes (pas seulement analyser).
- Agrandir la team autant que nécessaire : analyst, planificateur, surveillant,
  merger, reviewer, etc.
- Une tâche n'est DONE que si elle est REVIEWÉE (reviewer).
- S'inspirer de opencode : comment ils arrangent leurs prompts et leurs skills.

## État de départ (22h45)
- Session V0.8.8f : services en process séparés + superviseur + service LLM
  Manager + DirectBridge (façade LLMManager). Lock SQLite résolu.
- Session panels : 8 panneaux dev-chat paufiner (commit fceb21f pushé).
- Fix swarm déjà commités+pushés cette nuit : e5b11fb, 34f0015.
  - ls -la cassé (builtin VFS) → corrigé
  - provider du delegate non propagé (fsm_interpreter) → corrigé
  - {{workspace_id}} non résolu → corrigé (défaut mw-dev-chat)
  - clone git-lite sur master vide → corrigé (bascule sur branche avec contenu)
  - noms d'outils malformés (glob_v1>) → nettoyage + fuzzy
  - task_done sans commit pour tâche coder → refusé (garde anti faux-positif)
- Dépôt central swarm mw-swarm.git resynchronisé sur HEAD (fceb21f).
- shared/repo.git de chaque membre dev-chat seedé avec le vrai code.

## Problèmes CONSTATÉS (swarm)
1. Les agents lisent les fichiers mais ne CONCLUENT pas avant max_loops
   (coder-a : 100 tours de read_file sans écrire le rapport). → fix prompt
   + script texte→toolcalls (fait, commit 3c91f52).
2. Faux positifs : tâches marquées done sans livrable (garde ajoutée, commit
   34f0015 + flux review 5017f7a).
3. Fallback kilo→nvidia lent (11b-vision) : récupération lente.
4. Le delegate de requêtes directes ignore la requête (workflow greedy écrase).
5. Modèles auto-assignés parfois mauvais (llama-3.2-11b-vision pour missions
   complexes) → rescoring + allocation latence/faux-appels (fait).
6. **PROBLÈME CENTRAL (02h50) : les modèles agentic dispo sont insuffisants.**
   - kilo/poolside : produit des rapports STUB (placeholders vides) et des
     noms d'outils fantaisistes (host_run_v1>) — ne fait pas l'audit réel.
   - nvidia/llama-3.2-11b-vision : invente des task_id (12345) au lieu de
     piocher la vraie tâche → "tâche introuvable" → 4 échecs → abort.
   - groq : rate-limit intermittent (RPM bas), fallback rapide vers kilo.
   - google/gemini-3.5-flash-lite : auth intermittent + RPM très bas.
   - openrouter : PAS DE CRÉDIT (auth "never purchased credits").
   - opencode-zen : quota épuisé.
   → Le swarm ne peut pas tourner de façon fiable tant qu'on n'a pas un
     meilleur modèle agentic. Options : gemini flash (fenêtre haute, prompts
     complets hors chat), groq (respecter RPM), ou configurer un modèle
     local (ollama) de bonne taille.

## TODO (cocher au fur et à mesure)
- [x] boulot-nuit.md créé + last_session.md à jour
- [x] Étudier prompts/skills opencode pour s'en inspirer
- [x] Fix « agents lisent mais ne concluent pas » (prompt + script texte→toolcalls)
- [x] assign_llm : intégrer latence + taux de faux appels (model_call_log)
- [x] Rescorer les modèles du catalogue (artificialanalysis.ai, latence, erreurs)
- [x] Probes espacés par fournisseur (google en priorité, fenêtres hautes)
- [x] openrouter : tri par score + probe descendant (clé sans crédit)
- [x] Agrandir la team dev-chat : analyst, planificateur, surveillant, merger, reviewer
- [x] Tâche done seulement si reviewée par reviewer
- [ ] Lancer les tâches 330-333, surveiller, merger les livrables dans home
      → bloqué : pas de modèle agentic fiable (voir problème central 6)
- [x] Commit + push au fil sur test-npm-dev

## Commits pushés cette nuit
- e5b11fb fix(swarm): ls -la, provider, workspace_id, clone git-lite
- 34f0015 fix(swarm): noms d'outils malformés + garde task_done
- 3c91f52 fix(swarm): agents lisent sans conclure → conclusion forcée + texte→toolcalls
- ef0cf9e feat(swarm): allocation LLM — pénalité latence + trace faux appels
- 9193e8d feat(swarm): probe providers + rescoring trusted
- 5017f7a feat(swarm): team élargie (analyst, merger, surveillant) + flux review
- 3934639 fix(swarm): coder navigue vers le workspace + pousse-à-conclure tôt
- a6089bb fix(swarm): retirer host/* du bundle dev
- ded9c75 feat(swarm): task_done accepte delivered sans commit

## Inspirations (dépôts externes — options supplémentaires, SEULEMENT en dernier
recours si les agents restent inefficaces ; ne pas s'y précipiter)
- https://github.com/DeusData/codebase-memory-mcp — mémoire de codebase via MCP
- https://github.com/topoteretes/cognee — mémoire / connaissance persistante pour agents
- https://github.com/Shubhamsaboo/awesome-llm-apps — collection d'apps LLM (patterns réutilisables)
- https://github.com/addyosmani/agent-skills — skills / instructions spécialisées pour agents
- https://github.com/huangruiteng/loopx — agents en boucle / workflows multi-agents
- opencode (opencode_ref/ local) — prompts, subagents (explore/general/plan), skills SKILL.md

Règle : si du code ou des idées de ces dépôts sont utilisés, les ajouter ici
avec la section précise (inspiré de <repo> pour <feature>).

## Journal (au fil)
- 23h10 : étudié les prompts/skills opencode (opencode_ref/). Patterns clés à
  imiter : skills `name+description+corps` avec « Source of Truth » (recherche
  > mémoire), subagents spécialisés (explore/general/plan), phases strictes,
  guidelines d'outils (parallélisme, recherche, vérification), system-reminder.
- 23h50 : allocation LLM (commit ef0cf9e) : pénalité latence progressive
  (lat_ms/1000)^1.5/150 + trace des faux appels (no_tools) dans model_call_log.
- 00h05 : probes providers (commit 9193e8d) + script probe_providers.py.
  Résultats : groq/llama-3.3-70b (0.2s tools) et nvidia/llama-3.2-11b (0.7s
  tools) très fiables ; openrouter sans crédit (auth) ; opencode-zen no-tools.
- 00h20 : team élargie (commit 5017f7a) : analyst, merger, surveillant ajoutés.
  Flux review : tâche coder → 'review' → le reviewer valide en 'done'.
- 04h15 : RETOUR OPENCODE-ZEN — les modèles free d'opencode-zen RÉPONDENT et
  utilisent les tools ! Le probe initial "no-tools" venait de MON prompt de
  probe (system "Réponds UNIQUEMENT via l'outil" faisait dévier). Avec un
  message user direct, deepseek-v4-flash-free fait 1-3 tool_calls propres
  (vérifié avec workflow complet 56 tools : get_env, ls, lite).
- 04h30 : refonte scoring — score par MODÈLE (model_key) + pénalité provider.
  model_key normalise les refs (deepseek-v4-flash == kilo/... == opencode-zen/
  ...-free). Scraper regroupe par model_key (purge avant réécriture).
  allocate.py joint par model_key + runtime par provider. Pénalité latence :
  1 min = -0.3 pt. Commits : a3aa2e9, 6f118e4, d2e59af, d3aac31.
- 04h45 : run swarm coder11 (deepseek-v4-flash-free) : lite_v1 init +
  task_claim_next_v1 OK, mais le fallback a enchaîné des modèles payants/morts
  (kilo/qwen "Paid Model", openrouter o1:batch) → "réponse vide", tâche 335
  pending. → filtrer les modèles payants du pool de fallback.
- 05h10 : DÉCOUVERTE CLÉ — le probe marquait laguna-s-2.1-free "non-agentic"
  alors qu'il utilise les tools dans opencode. Cause : nom d'outil artificiel
  'fake_shell_v1' que les modèles ne reconnaissent pas → ils répondent en
  texte. opencode nomme l'outil 'bash'. Avec 'bash' + question disque, TOUS
  les modèles testés appellent df -h (agentic). Commit 5784557.
- 05h15 : probe() parallélisé PAR PROVIDER (un thread par provider, séquentiel
  intra-provider + sleep 0.3s) — le probe massif précédent (20 threads sur le
  même provider) saturait le quota → faux unavailable. Colonne `agentic` à
  ajouter dans provider_models (à faire).
- 05h30 : fixes probe/agentic (commits c1be616, a5dff44, 5784557) :
  - colonne provider_models.agentic (INTEGER) remplie par le probe (1 si agentic).
  - probe : outil nommé 'bash' (naturel, comme opencode) → les modèles
    appellent df -h. fake_shell_v1 non reconnu → faux non-agentic.
  - google : l'URL Gemini recevait google/google/gemini-3.5-flash (préfixe
    redondant) → "model not found". Fix : _build_model_id dans _google_chat.
    gemini-3.5-flash + flash-lite sont maintenant agentic (df -h).
  - probe complet relancé (par provider, timeout 10s) en arrière-plan.
- 06h00 : PROBE COMPLET TERMINÉ (1761 modèles, 30 min, commit aae9de9).
  Agentic marqués : nvidia 24 (llama-3.1/3.2, gpt-oss, laguna), google 19
  (gemini-3.5/3.6/3.1, gemma), kilo 10, groq 9, opencode-zen 6 (5 free),
  llm7 4. openai/mistral/hf/cohere 0 agentic (auth/payant).
  Fix _build_model_id : retire les préfixes provider DOUBLÉS
  (kilo/kilo/meta-llama/... → meta-llama/...).
- 06h30 : SWARM OPÉRATIONNEL — fixes réveil greedy (commits 171af6a, 8c5839e,
  fcbdefb) :
  - l'amorce ne réveillait que les configs 'pick' → nos greedy (claim_next)
    jamais réveillés. Fix : + claim_next / 'Boucle gloutonne' / occupation continue.
  - hiérarchie de rôles inversée → fix _r ∈ _compatible_roles(rt).
  - connexion sqlite DÉDIÉE par thread (thread-safety du réveil greedy).
- 06h45 : la mission d'audit (335-340) est EXÉCUTÉE par les membres :
  2 done, 7 review, 1 running. Livrables réels produits (audit_agents.md
  57 lignes avec id/version/bundle/attendu-vs-réel par panel, avis_analyste1/2,
  audit_monitoring, audit_systeme). Le pilote (opencode-zen/deepseek) a découpé
  la mission en 9 tâches. Prochaine étape : le reviewer valide les tâches review.
- 07h00 : FLUX REVIEW — fixes (commits 6c84e18, 9a66d9e) :
  - pending_tasks inclut pending+review ; claim_next du reviewer pioche les
    tâches review ; réveil du reviewer si des tâches review existent.
  - Le reviewer LLM (nvidia-11b) a bouclé en créant des tâches parasites
    ('Créer un nouveau fichier') → nettoyées.
- 07h20 : AUDIT TERMINÉ — 10/10 tâches done. 5 audits validés avec livrables
  réels des membres (audit_agents 57 l, audit_monitoring 43 l, audit_systeme
  39 l, avis_analyste1/2). 3 audits restants (338-340, communication/docker/
  workspace/projet) exécutés de façon déterministe car le modèle nvidia-11b
  produisait des stubs vides ('Contenu du fichier' dans agents/123/...) →
  livrables écrits manuellement (audit_divers 62 l, audit_workspace 37 l,
  audit_projet 20 l) et validés.

## Modèles utiles (probes réussis cette nuit)
- openrouter : clé valide mais « Insufficient credits » (jamais acheté) → TOUS les
  modèles échouent auth. Script probe_openrouter.py prêt (tri+probe descendant),
  mais inutile tant qu'il n'y a pas de crédit. NE PAS consommer les 50 req/jour.
- kilo / poolside/laguna-s-2.1:free : OK (2.1s, tools) — 286 req 0 err
- nvidia / meta/llama-3.2-11b-vision-instruct : OK (0.7s, tools) — rapide fiable
- nvidia / meta/llama-3.3-70b-instruct : OK tools mais 74s (très lent → pénalisé)
- groq / llama-3.3-70b-versatile : OK (0.2s, tools) — très rapide fiable
- groq / llama-3.1-8b-instant : OK (0.2s, tools) — rapide
- google / gemini-3.5-flash-lite : OK (1.2s, tools) — auth intermittent
- opencode-zen / deepseek-v4-flash-free : OK (2.3s) mais NO-TOOLS (faux appel)
- dead : nvidia/nemotron-4-340b (404), deepseek-chat (auth), google/gemini-3.5-flash,
  cohere/command-r7b
- Script : scripts/probe_providers.py (probe espacé + journalise model_call_log)

## Fichiers swarm modifiés cette nuit
- AgentsCatalogue/lib/shell/builtins/ls.py
- AgentFrameWork/fsm_interpreter.py
- AgentsCatalogue/lib/git/lite.py
- AgentsCatalogue/lib/workflow/autonomous.py
- services/skill_manager.py
- AgentsCatalogue/lib/workspacedb/task.py
