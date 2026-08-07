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
   + script texte→toolcalls.
2. Faux positifs : tâches marquées done sans livrable (garde ajoutée, à vérifier).
3. Fallback kilo→nvidia lent (11b-vision) : récupération lente.
4. Le delegate de requêtes directes ignore la requête (workflow greedy écrase).
5. Modèles auto-assignés parfois mauvais (llama-3.2-11b-vision pour missions
   complexes) → rescoring + allocation latence/faux-appels.

## TODO (cocher au fur et à mesure)
- [ ] boulot-nuit.md créé + last_session.md à jour
- [ ] Étudier prompts/skills opencode pour s'en inspirer
- [ ] Fix « agents lisent mais ne concluent pas » (prompt + script texte→toolcalls)
- [ ] assign_llm : intégrer latence + taux de faux appels (model_call_log)
- [ ] Rescorer les modèles du catalogue (artificialanalysis.ai, latence, erreurs)
- [ ] Probes espacés par fournisseur (google en priorité, fenêtres hautes)
- [ ] openrouter : tri par score + probe descendant (50 req/jour max)
- [ ] Agrandir la team dev-chat : analyst, planificateur, surveillant, merger, reviewer
- [ ] Tâche done seulement si reviewée par reviewer
- [ ] Lancer les tâches 330-333, surveiller, merger les livrables dans home
- [ ] Commit + push au fil sur test-npm-dev

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
- 23h30 : fix agents qui lisent sans conclure (commit 3c91f52 pushé) :
  - conclusion forcée après 5 tours de lecture pure sans outil d'écriture
  - script texte→toolcalls (_extract_toolcalls_from_text) : parse les toolcalls
    sérialisés dans la réponse texte des LLM descriptifs (poolside) et les exécute.
- À faire ensuite : assign_llm latence/faux-appels, rescoring, team élargie,
  reviewer pour done, relancer les tâches 330-333.

## Modèles utiles (probes réussis cette nuit)
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
