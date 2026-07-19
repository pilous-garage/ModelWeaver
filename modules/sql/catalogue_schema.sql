-- ModelWeaver Catalogue Database Schema
-- Fichier: .modelweaver/catalogue.db
-- Référence publique, peut être synchronisée depuis une BDD distante (Turso).

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

-- ============================================================
-- 1. CATALOGUE_PROVIDERS — Fournisseurs LLM (référence)
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_providers (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    ref                TEXT UNIQUE NOT NULL,
    name               TEXT NOT NULL,
    provider_type      TEXT NOT NULL CHECK(provider_type IN ('cloud', 'local', 'ollama', 'builtin')),
    api_type           TEXT,
    website            TEXT,
    is_free_tier_provider INTEGER DEFAULT 0,
    created_at         INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 1b. SEED catalogue_providers — catalogue initial complet
--     (cloud/local/ollama/builtin). INSERT OR IGNORE → idempotent.
--     L'ajout/suppression de providers se fait ensuite via la GUI
--     (add_provider) directement en BDD, pas via ce fichier.
-- ============================================================
INSERT OR IGNORE INTO catalogue_providers
    (ref, name, provider_type, api_type, website, is_free_tier_provider)
VALUES
    ('test_prov', 'Test Provider', 'local', 'openai_compatible', NULL, 0),
    ('ollama', 'Ollama', 'ollama', 'ollama', 'http://localhost:11434', 1),
    ('builtin', 'ModelWeaver Builtin', 'builtin', 'openai_compatible', NULL, 1),
    ('openai', 'OpenAI', 'cloud', 'openai_compatible', 'https://platform.openai.com', 0),
    ('anthropic', 'Anthropic', 'cloud', 'anthropic', 'https://anthropic.com', 0),
    ('google', 'Google', 'cloud', 'gemini', 'https://ai.google.dev', 1),
    ('groq', 'Groq', 'cloud', 'openai_compatible', 'https://groq.com', 1),
    ('mistral', 'Mistral', 'cloud', 'openai_compatible', 'https://mistral.ai', 1),
    ('deepseek', 'DeepSeek', 'cloud', 'openai_compatible', 'https://deepseek.com', 0),
    ('cohere', 'Cohere', 'cloud', 'cohere', 'https://cohere.com', 0),
    ('openrouter', 'OpenRouter', 'cloud', 'openai_compatible', 'https://openrouter.ai', 1),
    ('github-models', 'GitHub Models', 'cloud', 'openai_compatible', 'https://github.com/models', 1),
    ('togetherai', 'Together AI', 'cloud', 'openai_compatible', 'https://together.ai', 0),
    ('fireworks-ai', 'Fireworks AI', 'cloud', 'openai_compatible', 'https://fireworks.ai', 0),
    ('perplexity', 'Perplexity', 'cloud', 'openai_compatible', 'https://perplexity.ai', 0),
    ('huggingface', 'HuggingFace', 'cloud', 'openai_compatible', 'https://huggingface.co', 1),
    ('nvidia', 'NVIDIA', 'cloud', 'openai_compatible', 'https://build.nvidia.com', 1),
    ('cerebras', 'Cerebras', 'cloud', 'openai_compatible', 'https://cerebras.ai', 1),
    ('azure', 'Azure', 'cloud', 'azure', 'https://azure.microsoft.com', 0),
    ('amazon-bedrock', 'Amazon Bedrock', 'cloud', 'bedrock', 'https://aws.amazon.com/bedrock', 0),
    ('google-vertex', 'Google Vertex', 'cloud', 'vertex', 'https://cloud.google.com/vertex-ai', 0),
    ('lmstudio', 'LM Studio', 'cloud', 'openai_compatible', 'https://lmstudio.ai', 1),
    ('requesty', 'Requesty', 'cloud', 'openai_compatible', 'https://requesty.ai', 1),
    ('github-copilot', 'GitHub Copilot', 'cloud', 'openai_compatible', 'https://github.com/features/copilot', 0),
    ('xai', 'xAI', 'cloud', 'openai_compatible', 'https://x.ai', 0),
    ('databricks', 'Databricks', 'cloud', 'databricks', 'https://databricks.com', 0),
    ('cloudflare-workers-ai', 'Cloudflare Workers AI', 'cloud', 'openai_compatible', 'https://cloudflare.com', 1),
    ('scaleway', 'Scaleway', 'cloud', 'openai_compatible', 'https://scaleway.com', 0),
    ('ovhcloud', 'OVHcloud', 'cloud', 'openai_compatible', 'https://ovhcloud.com', 0),
    ('deepinfra', 'DeepInfra', 'cloud', 'openai_compatible', 'https://deepinfra.com', 0),
    ('anyapi', 'AnyAPI', 'cloud', 'openai_compatible', NULL, 0),
    ('abacus', 'Abacus', 'cloud', 'openai_compatible', NULL, 0),
    ('aihubmix', 'Aihubmix', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba', 'Alibaba', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba-cn', 'Alibaba CN', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba-coding-plan', 'Alibaba Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba-coding-plan-cn', 'Alibaba Coding Plan CN', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba-token-plan', 'Alibaba Token Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('alibaba-token-plan-cn', 'Alibaba Token Plan CN', 'cloud', 'openai_compatible', NULL, 0),
    ('ambient', 'Ambient', 'cloud', 'openai_compatible', NULL, 0),
    ('atomic-chat', 'Atomic Chat', 'cloud', 'openai_compatible', NULL, 0),
    ('auriko', 'Auriko', 'cloud', 'openai_compatible', NULL, 0),
    ('azure-cognitive-services', 'Azure Cognitive Services', 'cloud', 'openai_compatible', NULL, 0),
    ('bailing', 'Bailing', 'cloud', 'openai_compatible', NULL, 0),
    ('baseten', 'Baseten', 'cloud', 'openai_compatible', NULL, 0),
    ('berget', 'Berget', 'cloud', 'openai_compatible', NULL, 0),
    ('chutes', 'Chutes', 'cloud', 'openai_compatible', NULL, 0),
    ('clarifai', 'Clarifai', 'cloud', 'openai_compatible', NULL, 0),
    ('cloudferro-sherlock', 'Cloudferro Sherlock', 'cloud', 'openai_compatible', NULL, 0),
    ('cloudflare-ai-gateway', 'Cloudflare AI Gateway', 'cloud', 'openai_compatible', NULL, 0),
    ('cortecs', 'Cortecs', 'cloud', 'openai_compatible', NULL, 0),
    ('crof', 'Crof', 'cloud', 'openai_compatible', NULL, 0),
    ('digitalocean', 'DigitalOcean', 'cloud', 'openai_compatible', NULL, 0),
    ('dinference', 'Dinference', 'cloud', 'openai_compatible', NULL, 0),
    ('drun', 'Drun', 'cloud', 'openai_compatible', NULL, 0),
    ('evroc', 'Evroc', 'cloud', 'openai_compatible', NULL, 0),
    ('fastrouter', 'Fastrouter', 'cloud', 'openai_compatible', NULL, 0),
    ('friendli', 'Friendli', 'cloud', 'openai_compatible', NULL, 0),
    ('frogbot', 'Frogbot', 'cloud', 'openai_compatible', NULL, 0),
    ('gmicloud', 'GMICloud', 'cloud', 'openai_compatible', NULL, 0),
    ('gitlab', 'GitLab', 'cloud', 'openai_compatible', NULL, 0),
    ('helicone', 'Helicone', 'cloud', 'openai_compatible', NULL, 0),
    ('hpc-ai', 'HPC AI', 'cloud', 'openai_compatible', NULL, 0),
    ('iflowcn', 'Iflowcn', 'cloud', 'openai_compatible', NULL, 0),
    ('inception', 'Inception', 'cloud', 'openai_compatible', NULL, 0),
    ('inceptron', 'Inceptron', 'cloud', 'openai_compatible', NULL, 0),
    ('inference', 'Inference', 'cloud', 'openai_compatible', NULL, 0),
    ('io-net', 'IO.net', 'cloud', 'openai_compatible', NULL, 0),
    ('jiekou', 'Jiekou', 'cloud', 'openai_compatible', NULL, 0),
    ('kenari', 'Kenari', 'cloud', 'openai_compatible', NULL, 0),
    ('kilo', 'Kilo', 'cloud', 'openai_compatible', NULL, 0),
    ('kimi-for-coding', 'Kimi for Coding', 'cloud', 'openai_compatible', NULL, 0),
    ('kuae-cloud-coding-plan', 'Kuae Cloud Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('llama', 'Llama', 'cloud', 'openai_compatible', NULL, 0),
    ('llmgateway', 'LLMGateway', 'cloud', 'openai_compatible', NULL, 0),
    ('llmtr', 'Llmtr', 'cloud', 'openai_compatible', NULL, 0),
    ('longcat', 'Longcat', 'cloud', 'openai_compatible', NULL, 0),
    ('lucidquery', 'Lucidquery', 'cloud', 'openai_compatible', NULL, 0),
    ('merge-gateway', 'Merge Gateway', 'cloud', 'openai_compatible', NULL, 0),
    ('minimax', 'Minimax', 'cloud', 'openai_compatible', NULL, 0),
    ('minimax-cn', 'Minimax CN', 'cloud', 'openai_compatible', NULL, 0),
    ('minimax-coding-plan', 'Minimax Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('minimax-cn-coding-plan', 'Minimax CN Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('mixlayer', 'Mixlayer', 'cloud', 'openai_compatible', NULL, 0),
    ('moark', 'Moark', 'cloud', 'openai_compatible', NULL, 0),
    ('modelscope', 'ModelScope', 'cloud', 'openai_compatible', 'https://modelscope.cn', 1),
    ('moonshotai', 'Moonshot AI', 'cloud', 'openai_compatible', NULL, 0),
    ('moonshotai-cn', 'Moonshot AI CN', 'cloud', 'openai_compatible', NULL, 0),
    ('morph', 'Morph', 'cloud', 'openai_compatible', NULL, 0),
    ('nano-gpt', 'Nano GPT', 'cloud', 'openai_compatible', NULL, 0),
    ('nearai', 'Near AI', 'cloud', 'openai_compatible', NULL, 0),
    ('nebius', 'Nebius', 'cloud', 'openai_compatible', NULL, 0),
    ('neon', 'Neon', 'cloud', 'openai_compatible', NULL, 0),
    ('neuralwatt', 'Neuralwatt', 'cloud', 'openai_compatible', NULL, 0),
    ('nova', 'Nova', 'cloud', 'openai_compatible', NULL, 0),
    ('novita-ai', 'Novita AI', 'cloud', 'openai_compatible', NULL, 0),
    ('ollama-cloud', 'Ollama Cloud', 'cloud', 'openai_compatible', NULL, 0),
    ('opencode', 'Opencode', 'cloud', 'openai_compatible', NULL, 0),
    ('opencode-go', 'Opencode Go', 'cloud', 'openai_compatible', NULL, 0),
    ('orcarouter', 'OrcaRouter', 'cloud', 'openai_compatible', NULL, 0),
    ('perplexity-agent', 'Perplexity Agent', 'cloud', 'openai_compatible', NULL, 0),
    ('poe', 'Poe', 'cloud', 'openai_compatible', NULL, 0),
    ('poolside', 'Poolside', 'cloud', 'openai_compatible', NULL, 0),
    ('privatemode-ai', 'Privatemode AI', 'cloud', 'openai_compatible', NULL, 0),
    ('qihang-ai', 'Qihang AI', 'cloud', 'openai_compatible', NULL, 0),
    ('qiniu-ai', 'Qiniu AI', 'cloud', 'openai_compatible', NULL, 0),
    ('regolo-ai', 'Regolo AI', 'cloud', 'openai_compatible', NULL, 0),
    ('routing-run', 'Routing Run', 'cloud', 'openai_compatible', NULL, 0),
    ('sakana', 'Sakana', 'cloud', 'openai_compatible', NULL, 0),
    ('sap-ai-core', 'SAP AI Core', 'cloud', 'openai_compatible', NULL, 0),
    ('sarvam', 'Sarvam', 'cloud', 'openai_compatible', NULL, 0),
    ('siliconflow', 'SiliconFlow', 'cloud', 'openai_compatible', 'https://siliconflow.cn', 1),
    ('siliconflow-cn', 'SiliconFlow CN', 'cloud', 'openai_compatible', NULL, 0),
    ('snowflake-cortex', 'Snowflake Cortex', 'cloud', 'openai_compatible', NULL, 0),
    ('stackit', 'StackIT', 'cloud', 'openai_compatible', NULL, 0),
    ('stepfun', 'StepFun', 'cloud', 'openai_compatible', NULL, 0),
    ('stepfun-ai', 'StepFun AI', 'cloud', 'openai_compatible', NULL, 0),
    ('subconscious', 'Subconscious', 'cloud', 'openai_compatible', NULL, 0),
    ('submodel', 'Submodel', 'cloud', 'openai_compatible', NULL, 0),
    ('synthetic', 'Synthetic', 'cloud', 'openai_compatible', NULL, 0),
    ('tencent-coding-plan', 'Tencent Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('tencent-token-plan', 'Tencent Token Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('tencent-tokenhub', 'Tencent Tokenhub', 'cloud', 'openai_compatible', NULL, 0),
    ('the-grid-ai', 'The Grid AI', 'cloud', 'openai_compatible', NULL, 0),
    ('tinfoil', 'Tinfoil', 'cloud', 'openai_compatible', NULL, 0),
    ('trustedrouter', 'TrustedRouter', 'cloud', 'openai_compatible', NULL, 0),
    ('umans-ai', 'Umans AI', 'cloud', 'openai_compatible', NULL, 0),
    ('umans-ai-coding-plan', 'Umans AI Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('upstage', 'Upstage', 'cloud', 'openai_compatible', NULL, 0),
    ('v0', 'v0', 'cloud', 'openai_compatible', NULL, 0),
    ('venice', 'Venice', 'cloud', 'openai_compatible', NULL, 0),
    ('vercel', 'Vercel', 'cloud', 'openai_compatible', NULL, 0),
    ('vivgrid', 'Vivgrid', 'cloud', 'openai_compatible', NULL, 0),
    ('vultr', 'Vultr', 'cloud', 'openai_compatible', NULL, 0),
    ('wafer.ai', 'Wafer AI', 'cloud', 'openai_compatible', NULL, 0),
    ('wandb', 'Wandb', 'cloud', 'openai_compatible', NULL, 0),
    ('xiaomi', 'Xiaomi', 'cloud', 'openai_compatible', NULL, 0),
    ('xiaomi-token-plan-ams', 'Xiaomi Token Plan AMS', 'cloud', 'openai_compatible', NULL, 0),
    ('xiaomi-token-plan-cn', 'Xiaomi Token Plan CN', 'cloud', 'openai_compatible', NULL, 0),
    ('xiaomi-token-plan-sgp', 'Xiaomi Token Plan SGP', 'cloud', 'openai_compatible', NULL, 0),
    ('zai', 'ZAI', 'cloud', 'openai_compatible', NULL, 0),
    ('zai-coding-plan', 'ZAI Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('zeldoc', 'Zeldoc', 'cloud', 'openai_compatible', NULL, 0),
    ('zenmux', 'Zenmux', 'cloud', 'openai_compatible', NULL, 0),
    ('zhipuai', 'ZhipuAI', 'cloud', 'openai_compatible', NULL, 0),
    ('zhipuai-coding-plan', 'ZhipuAI Coding Plan', 'cloud', 'openai_compatible', NULL, 0),
    ('claudinio', 'Claudinio', 'cloud', 'openai_compatible', NULL, 0),
    ('google-vertex-anthropic', 'Google Vertex Anthropic', 'cloud', 'openai_compatible', NULL, 0),
    ('abliteration-ai', 'Abliteration AI', 'cloud', 'openai_compatible', NULL, 0),
    ('xpersona', 'Xpersona', 'cloud', 'openai_compatible', NULL, 0),
    ('302ai', '302AI', 'cloud', 'openai_compatible', NULL, 0),
    ('freemodel', 'Freemodel', 'cloud', 'openai_compatible', NULL, 0),
    ('lilac', 'Lilac', 'cloud', 'openai_compatible', NULL, 0),
    ('meganova', 'Meganova', 'cloud', 'openai_compatible', NULL, 0);

-- ============================================================
-- 1c. PROVIDER_ENDPOINTS — Endpoints API possédés par le provider.
--     1 provider → N endpoints (base URL canonique + surfaces :
--     chat, embeddings, models…, ou bases régionales multiples).
--     Les clés peuvent encore OVERRIDE via api_base (proxy / self-host
--     / gateway entreprise) — c'est un cas légitime par clé.
-- ============================================================
CREATE TABLE IF NOT EXISTS provider_endpoints (
    endpoint_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id   INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
    label         TEXT NOT NULL,
    endpoint_url  TEXT NOT NULL,
    api_type      TEXT,
    is_default    INTEGER DEFAULT 0,
    local_latency REAL,
    global_quality REAL,
    created_at    INTEGER DEFAULT (strftime('%s','now'))
);
CREATE INDEX IF NOT EXISTS idx_provider_endpoints_provider ON provider_endpoints(provider_id);

-- Seed des endpoints canoniques (INSERT OR IGNORE → idempotent).
-- Sélectionne provider_id via ref pour ne seeder que si le provider existe.
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.openai.com/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='openai';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.anthropic.com', 'anthropic', 1 FROM catalogue_providers WHERE ref='anthropic';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1beta', 'https://generativelanguage.googleapis.com/v1beta', 'gemini', 1 FROM catalogue_providers WHERE ref='google';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'openai/v1', 'https://api.groq.com/openai/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='groq';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.mistral.ai/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='mistral';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.deepseek.com/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='deepseek';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.cohere.com/v1', 'cohere', 1 FROM catalogue_providers WHERE ref='cohere';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://openrouter.ai/api/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='openrouter';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.together.xyz/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='togetherai';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'inference/v1', 'https://api.fireworks.ai/inference/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='fireworks-ai';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.perplexity.ai', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='perplexity';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://integrate.api.nvidia.com/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='nvidia';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api-inference.huggingface.co', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='huggingface';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://models.in.ai', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='github-models';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.scaleway.ai/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='scaleway';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'https://api.endpoints.ai/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='ovhcloud';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'http://localhost:11434', 'ollama', 1 FROM catalogue_providers WHERE ref='ollama';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'v1', 'http://localhost:1234/v1', 'openai_compatible', 1 FROM catalogue_providers WHERE ref='lmstudio';
-- Endpoints templatés (région/resource variables) — base de référence.
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'resource', 'https://{resource}.openai.azure.com', 'azure', 1 FROM catalogue_providers WHERE ref='azure';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'region', 'https://bedrock-runtime.{region}.amazonaws.com', 'bedrock', 1 FROM catalogue_providers WHERE ref='amazon-bedrock';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'region', 'https://{region}-aiplatform.googleapis.com/v1', 'vertex', 1 FROM catalogue_providers WHERE ref='google-vertex';
INSERT OR IGNORE INTO provider_endpoints (provider_id, label, endpoint_url, api_type, is_default)
SELECT id, 'workspace', 'https://{workspace}.databricks.com/serving-endpoints', 'databricks', 1 FROM catalogue_providers WHERE ref='databricks';

-- ============================================================
-- 2. CATALOGUE_MODELS — Modèles LLM (référence)
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_models (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    name            TEXT NOT NULL,
    developer       TEXT,
    release_year    INTEGER,
    architecture    TEXT,
    parameter_count TEXT,
    modality        TEXT,
    target_use      TEXT,
    license         TEXT,
    is_open_weights INTEGER DEFAULT 0,
    parent_model_ref TEXT,
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 3. PROVIDER_MODELS — Jointure provider ↔ modèle (prix, tokens)
-- ============================================================
CREATE TABLE IF NOT EXISTS provider_models (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id         INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
    model_id            INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
    provider_model_name TEXT NOT NULL,
    context_window_tokens INTEGER,
    max_output_tokens   INTEGER,
    cost_per_input_token    TEXT,
    cost_per_output_token   TEXT,
    context_window_effective INTEGER,
    status                  TEXT DEFAULT 'active' CHECK(status IN ('active','deprecated','experimental')),
    created_at          INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at          INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(provider_id, model_id)
);

-- ============================================================
-- 4. CONTEXT_AUDIT_LOG — Journal des dépassements de contexte
-- ============================================================
CREATE TABLE IF NOT EXISTS context_audit_log (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_ref          TEXT NOT NULL,
    model_ref             TEXT NOT NULL,
    tokens_sent           INTEGER NOT NULL,
    detected_context_limit INTEGER,
    context_window_effective INTEGER,
    created_at            INTEGER DEFAULT (strftime('%s', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_provider_model ON context_audit_log(provider_ref, model_ref);

-- ============================================================
-- 5. CATALOGUE_COMMANDS — Commandes utilitaires
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_commands (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    description TEXT,
    command_type TEXT NOT NULL CHECK(command_type IN ('system', 'binary', 'project', 'utility')),
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 5. CLASSES_OUTILS — Taxonomie métier des outils
--    (language, dev-tool, ide, chat-llm, agent, engine, router,
--     context, system, other). Différent du tool_type technique
--    (binary/python-module/...) qui reste dans catalogue_outils.
-- ============================================================
CREATE TABLE IF NOT EXISTS classes_outils (
    classe_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    ref         TEXT UNIQUE NOT NULL,
    nom         TEXT NOT NULL,
    description TEXT,
    sort_order  INTEGER DEFAULT 0,
    created_at  INTEGER DEFAULT (strftime('%s', 'now'))
);

INSERT OR IGNORE INTO classes_outils (ref, nom, description, sort_order) VALUES
    ('language',  'Languages',       'Interpréteurs et compilateurs',           10),
    ('dev-tool',  'Dev Tools',       'Outils de développement',                 20),
    ('ide',       'IDEs',            'Environnements de développement intégrés', 30),
    ('chat-llm',  'Chat LLM',        'Interfaces de chat avec les LLM',         40),
    ('agent',     'Agents',          'Orchestrateurs IA autonomes',             50),
    ('engine',    'LLM Engines',     'Moteurs d''exécution locale de LLM',      60),
    ('router',    'Routers',         'Passerelles et proxy LLM',                70),
    ('context',   'Context Tools',   'Gestion du contexte et secrets',          80),
    ('system',    'System Tools',    'Utilitaires système',                     90),
    ('other',     'Other',           'Autres outils',                           999);

-- ============================================================
-- 6. CATALOGUE_OUTILS — Entrée catalogue par outil
--    default_download_url / allowed_platforms / allowed_arches
--    sont désormais INFÉRÉS des recettes (catalogue_recettes).
--    install_method est dans catalogue_recettes.manager.
--    tool_type (binary/python-module/...) reste pour le routing installer.
--    classe_outil_id pointe vers la classe métier (classes_outils).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_outils (
    outil_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    ref             TEXT UNIQUE NOT NULL,
    nom             TEXT NOT NULL,
    fabricant       TEXT,
    description     TEXT,
    tool_type       TEXT CHECK(tool_type IN ('binary','python-module','archive','source','container')),
    classe_outil_id INTEGER REFERENCES classes_outils(classe_id) ON DELETE SET NULL,
    created_at      INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 6. CATALOGUE_VERSIONS — Une version par outil
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_versions (
    version_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    outil_id    INTEGER NOT NULL REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
    nom_version TEXT NOT NULL,
    description TEXT,
    created_at  INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(outil_id, nom_version)
);

-- ============================================================
-- 7. CATALOGUE_RECETTES — Recette par (version, os, arch, manager)
--    content = corps de la recette (.mw.yaml du manager block)
--    Chaque colonne manager est l'install_method (pip, apt, binary…).
--    L'existence d'une recette pour un (os, arch) donné indique
--    la compatibilité (plus besoin de allowed_platforms/arches).
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_recettes (
    recette_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id      INTEGER NOT NULL REFERENCES catalogue_versions(version_id) ON DELETE CASCADE,
    os              TEXT NOT NULL DEFAULT 'all',
    arch            TEXT NOT NULL DEFAULT 'all',
    manager         TEXT,          -- install_method : pip, apt, binary, github-release…
    package         TEXT,          -- nom du paquet chez le manager (ex: litellm)
    confidence      REAL DEFAULT 1.0,
    createur_id     TEXT DEFAULT 'system',
    install_count   INTEGER DEFAULT 0,
    uninstall_count INTEGER DEFAULT 0,
    content         TEXT,
    enabled         INTEGER DEFAULT 1,
    created_at      INTEGER DEFAULT (strftime('%s', 'now')),
    updated_at      INTEGER DEFAULT (strftime('%s', 'now')),
    UNIQUE(version_id, os, arch, manager)
);

-- ============================================================
-- 8. OUTILS_POPULARITE — Table distante (agrégée par outil)
-- ============================================================
CREATE TABLE IF NOT EXISTS outils_popularite (
    outil_id      INTEGER PRIMARY KEY REFERENCES catalogue_outils(outil_id) ON DELETE CASCADE,
    nb_install    INTEGER DEFAULT 0,
    nb_desinstall INTEGER DEFAULT 0,
    updated_at    INTEGER DEFAULT (strftime('%s', 'now'))
);

-- ============================================================
-- 9. KEY_ENDPOINT_MODELS — Declaration joignable (key x endpoint x model)
--    Derivee du produit (endpoints du provider) x (keys du provider).
--    `declared` : present dans la liste-modele de l'API au dernier refresh.
--    `available` : joignable reellement (ping optionnel / degradation runtime).
--    On ne SUPPRIME pas : une ligne non re-declaree reste declared=0.
-- ============================================================
CREATE TABLE IF NOT EXISTS key_endpoint_models (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    provider_id   INTEGER NOT NULL REFERENCES catalogue_providers(id) ON DELETE CASCADE,
    endpoint_id   INTEGER NOT NULL REFERENCES provider_endpoints(endpoint_id) ON DELETE CASCADE,
    key_ref       TEXT NOT NULL,
    model_id      INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
    provider_model_name TEXT NOT NULL,
    declared      INTEGER DEFAULT 0,
    available     INTEGER DEFAULT 0,
    last_checked_at INTEGER,
    last_error    TEXT,
    created_at    INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(endpoint_id, key_ref, model_id)
);

-- ============================================================
-- 10. MODEL_EFFICACY — Efficacite communautaire (catalogue).
--     Vierge au depart ; remplie plus tard (analyse commune).
-- ============================================================
CREATE TABLE IF NOT EXISTS model_efficacy (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id      INTEGER NOT NULL REFERENCES catalogue_models(id) ON DELETE CASCADE,
    use_case      TEXT NOT NULL,
    score_quality   REAL DEFAULT 0,
    score_speed     REAL DEFAULT 0,
    score_cost      REAL DEFAULT 0,
    score_reliability REAL DEFAULT 0,
    samples       INTEGER DEFAULT 0,
    updated_at    INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(model_id, use_case)
);

-- ============================================================
-- 11. BUDGET_TAGS — Reference des types de budget.
-- ============================================================
CREATE TABLE IF NOT EXISTS budget_tags (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    code      TEXT NOT NULL UNIQUE,
    label     TEXT NOT NULL,
    unit      TEXT,
    scope     TEXT  -- requests | tokens | cost | latency
);

INSERT OR IGNORE INTO budget_tags (code, label, unit, scope) VALUES
    ('req_per_min',  'Requetes / minute',        'requests', 'requests'),
    ('req_per_day',  'Requetes / jour',          'requests', 'requests'),
    ('tok_per_min',  'Tokens / minute',          'tokens',   'tokens'),
    ('tok_per_hour', 'Tokens / heure',           'tokens',   'tokens'),
    ('tok_per_day',  'Tokens / jour',            'tokens',   'tokens'),
    ('cost_per_day', 'Cout / jour (USD)',        'usd',      'cost'),
    ('cost_per_month','Cout / mois (USD)',       'usd',      'cost');

-- ============================================================
-- 11b. CATALOGUE_ALIASES — Réconciliation des noms externes
--      (litellm, docs officiels, outils tiers) avec les refs
--      canoniques du catalogue (providers et modèles).
--      Ex: source='litellm_github', entity_type='provider',
--          alias='nvidia_nim' -> canonical_ref='nvidia'.
--      Une meme source peut avoir plusieurs alias pointant vers
--      la meme ref (1:N). La resolution prend le plus prioritaire.
-- ============================================================
CREATE TABLE IF NOT EXISTS catalogue_aliases (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    source        TEXT NOT NULL,
    entity_type   TEXT NOT NULL CHECK(entity_type IN ('provider','model')),
    alias         TEXT NOT NULL,
    canonical_ref TEXT NOT NULL,
    priority      INTEGER DEFAULT 0,
    created_at    INTEGER DEFAULT (strftime('%s','now')),
    UNIQUE(source, entity_type, alias)
);
CREATE INDEX IF NOT EXISTS idx_aliases_source_entity ON catalogue_aliases(source, entity_type);
CREATE INDEX IF NOT EXISTS idx_aliases_canonical ON catalogue_aliases(canonical_ref);

-- ============================================================
-- 12. BUDGETS — Limites theoriques (catalogue, partageable).
--     target_type : provider | model | endpoint | key
--     window : minute | hour | day | month (periode de reset de `used`)
-- ============================================================
CREATE TABLE IF NOT EXISTS budgets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type   TEXT NOT NULL CHECK(target_type IN ('provider','model','endpoint','key')),
    target_ref    TEXT NOT NULL,
    tag_id        INTEGER NOT NULL REFERENCES budget_tags(id),
    limit_value   REAL NOT NULL,
    window        TEXT NOT NULL DEFAULT 'day' CHECK(window IN ('minute','hour','day','month')),
    cost_per_unit REAL,
    created_at    INTEGER DEFAULT (strftime('%s','now'))
);

-- ============================================================
-- INDEXES
-- ============================================================
CREATE INDEX IF NOT EXISTS idx_cat_providers_ref ON catalogue_providers(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_ref ON catalogue_models(ref);
CREATE INDEX IF NOT EXISTS idx_cat_models_developer ON catalogue_models(developer);
CREATE INDEX IF NOT EXISTS idx_cat_commands_ref ON catalogue_commands(ref);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_provider ON provider_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_cat_provider_models_model ON provider_models(model_id);
CREATE INDEX IF NOT EXISTS idx_kem_provider ON key_endpoint_models(provider_id);
CREATE INDEX IF NOT EXISTS idx_kem_endpoint ON key_endpoint_models(endpoint_id);
CREATE INDEX IF NOT EXISTS idx_kem_key ON key_endpoint_models(key_ref);
CREATE INDEX IF NOT EXISTS idx_kem_declared ON key_endpoint_models(declared);
CREATE INDEX IF NOT EXISTS idx_kem_available ON key_endpoint_models(available);
CREATE INDEX IF NOT EXISTS idx_me_model ON model_efficacy(model_id);
CREATE INDEX IF NOT EXISTS idx_budgets_target ON budgets(target_type, target_ref);
CREATE INDEX IF NOT EXISTS idx_outils_ref ON catalogue_outils(ref);
CREATE INDEX IF NOT EXISTS idx_classes_ref ON classes_outils(ref);
CREATE INDEX IF NOT EXISTS idx_recettes_version ON catalogue_recettes(version_id);
