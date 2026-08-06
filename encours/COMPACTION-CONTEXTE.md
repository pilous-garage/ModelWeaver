# Compaction du contexte — ModelWeaver (V0.8.8, août 2026)

## But du projet
IDE self-hosted : chat de dev piloté par un agent maître (team dev-chat, agent
pilote, modes plan/build), streaming temps réel style opencode (texte/thinking +
bloc thinking cliquable). Swarm d'agents, catalogue de modèles, superviseur de
services, GUI Tauri v2.

## Architecture (état actuel)
- **Daemon HTTP** (services/api/daemon.py, port 8770, ThreadingHTTPServer) :
  route unique `/v1/*`, JSON enveloppé `{ok, route, result}` → lire `res.result.*`.
  Auth : `Authorization: Bearer <token>` (64 chars, `~/.modelweaver/api.token`).
  SSE : `register_streaming`/`STREAMING_ROUTES` (router.py), `_handle_stream`
  (daemon.py:710), handlers → `event: delta|result|done`.
  `_rollback_shared_dbs` au boot/erreur HTTP ≥ 400 (lock SQLite WAL).
- **Superviseur** (services/supervisor/) : process séparé par service (daemon,
  catalogue, installer, model-sync, tester, llm-manager, ressource_manager),
  boucle 5s, relance les morts sauf arrêt manuel, registre run/sockets.json.
  Le daemon relance le superviseur s'il meurt (supervision mutuelle).
- **LLMManager façade** (modules/llm_manager/) : get_bridge() (config
  `llm.bridge`, défaut `direct`), chat/chat_stream/list_available_models/
  classify_error. DirectBridge = implémentation directe multi-providers
  (openai, google/gemini, groq, openrouter, cohere, deepseek, huggingface…).
  Allocation via service LLM (services/llm_manager/) : probe réel (chat
  max_tokens=1) avant retour, exclut modèles en repos.
- **model_sync** (services/model_sync/) : synchroniseur 1x/h par clé API
  (config `models.sync_interval_hours`), autocommit, backoff 60s×2 plafond 24h,
  defunct après 5 échecs, re-probe 24h, réactive les modèles revenus.
- **GUI v2** (interfaces/main/GUI/v2/) : Tauri, `frontendDist: ../dist`,
  `npm run build`. Panels : dev-chat, monitoring-workspace, agents-team-members,
  agents-activity, etc. `ctx.api = { post, stream }` (App.tsx buildCtx).
  bridge.ts : daemonPost (JSON), daemonPostStream (POST+SSE, abort()).
  Panels externes servis depuis `~/.modelweaver/panels-dist/` SANS auth.
- **Autonomous** (AgentsCatalogue/lib/workflow/autonomous.py) : workflow à
  outils, garde-fou (modèle sans appel outil → status=failed), fallback
  rate-limit (backoff 10/25/45s puis bascule de provider, is_retryable=True),
  propagation thoughtSignature Gemini, stream_events → stream_bus
  (cross-process activé au daemon up).

## Session en cours — streaming dev-chat (fait)
- **direct_bridge.py** : `chat_stream_events()` yields
  `{"type": "thinking"|"content"|"tool_calls", "delta": …}`
  (`reasoning_content`, `content`, `delta.tool_calls`) ; `chat_stream()`
  agrège en ChatResponse ; `_google_chat_stream()` SSE Gemini
  (systemInstruction seulement si présent). `_log_call` sans kwarg `stream`.
- **autonomous.py** : `_stream_llm_round()` (SimpleNamespace équivalent
  ChatResponse), `_chat_with_tools(on_event, stream_events)`, `exec()` publie
  dans stream_bus (agent_id dérivé du home).
- **dev_chat.py** : routes `dev-chat/send` (sync) + **`dev-chat/stream`**
  (SSE, reset stream_bus, run en thread, drain → event delta/result/done,
  timeout 900s).
- **GUI** : bridge.ts `daemonPostStream` ; App.tsx `ctx.api.stream` ;
  panel communication-dev-chat : rendu progressif (liveIdx), **bloc thinking
  cliquable** (▸ Penser…/Pensé, toggle par index), badge ⚡ si streamed,
  `fmtWho` anti-doublon provider (retourne model seul si
  `model.startsWith("${p}/")`), repli `dev-chat/send`.
- Modèle testé : `opencode-zen/deepseek-v4-flash-free` (endpoint
  https://opencode.ai/zen/v1, api_type openai_compatible, réponse
  `reasoning_content` = thinking ; content peut être vide si max_tokens
  consommé par réflexion).
- E2E backend OK (curl/HTTP Bearer) : 99 deltas (98 thinking + 1 content) en
  ~110s puis result (status failed : guard-rail `glob_v1>` malformé, pas un
  bug streaming) + done.

## État BDD
- key_endpoint_models 1182, model_probe_state 1182, defunct ~68.
- Lock SQLite libre (écriture externe 0.000s pendant daemon+model_sync).
- 8/9 providers avec clé trouvent des modèles ; seul mistral échoue (401).

## Commits récents (branch test-npm-dev)
- `978cf18` feat(dev-chat): IDE chat de dev — team dev-chat + agent pilote
  plan/build + fixs backend
- `c88695e` feat(dev-chat): streaming temps réel + bloc thinking cliquable
  (SSE daemon → GUI v2) — PUISHÉ, le build distant doit pull + rebuild GUI.

## IMPORTANT — fenêtre GUI STALE
Le code GUI + `dist/` sont à jour (index-Q641Vlfa.js : stream/thinking/fmtWho
présents, build 22:34) mais la fenêtre Tauri tourne encore sur l'ANCIEN bundle
(ancien doublon opencode-zen/opencode-zen, réponse d'un coup, pas de thinking).
**Action : re-run `npm run build` / relancer la fenêtre Tauri avec le dist
frais.** En web : `npm run dev` (vite, port 5173) ou ouvrir dist/index.html.

## Prochaines étapes
1. Rebuild GUI côté build distant après pull (point ci-dessus).
2. Vérif visuelle : envoyer « hi ? » → thinking streamé + ⚡ + réponse
   progressive ; doublon de préfixe disparu.
3. Agents en process séparés (agent_manager threads → subprocess) — étape 3.
4. Rate-limit groq intermittent : marquer en repos si unavailable.
5. Tests unitaires supervisor/llm-manager/ressource_manager.
6. Seed dépôt central repos/mw-swarm.git + déclencheur team/delegate pour
   swarm-selfimprove.

## Fichiers clés
- modules/llm_manager/direct_bridge.py (streaming, _google_chat_stream)
- AgentsCatalogue/lib/workflow/autonomous.py (_stream_llm_round, exec)
- services/api/handlers/dev_chat.py (dev-chat/stream SSE)
- services/api/router.py + daemon.py:710 (_handle_stream)
- interfaces/main/GUI/v2/src/bridge.ts (daemonPostStream)
- interfaces/main/GUI/v2/src/App.tsx (ctx.api.stream)
- interfaces/main/GUI/v2/src/panels/communication-dev-chat.panel.tsx
- services/supervisor/{main,client}.py, services/model_sync/model_sync.py
- modules/sql/db.py (migrations sûres : rollback+commit dans _ensure_schema)
- VERSIONS.md, modelweaver.md, .opencode/last_session.md
