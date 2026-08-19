-- budget_cost.db — Sources NON-runtime des budgets/couts.
--
-- HIÉRARCHIE de priorité (pour le budget effectif) :
--   humain > general > guess   (guess = quotas seulement)
--
-- LIMITES (par budget) :
--   budget_reel  : la limite déclarée/réelle
--   budget_hard  : % de budget_reel — tout dépassement est REFUSÉ
--   budget_soft  : % de budget_reel — dépassement AUTORISÉ (tolérance burst)
--
-- cost_init / quota_init : data_types dans le catalogue local (peuplés via
-- buffer models.dev) — lus par ce domaine pour générer runtime_llm.budget_final.
--
-- Domaine OUVERT (cli, agents, daemon). Pas de token.
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);

-- human_budget : déclarations HUMAINES (priorité haute)
--   nb = limite ; scope -1 = tous. hard_limit/soft_limit en % de nb.
CREATE TABLE IF NOT EXISTS human_budget (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,        -- budget_tag (req_per_min, tok_per_day, cost_per_day…)
    nb          REAL NOT NULL,        -- limite réelle
    provider    TEXT DEFAULT '-1',
    modele      TEXT DEFAULT '-1',
    project     TEXT DEFAULT '-1',
    team        TEXT DEFAULT '-1',
    agent       TEXT DEFAULT '-1',
    hard_limit  REAL DEFAULT 100.0,   -- % de nb : refus si dépassé
    soft_limit  REAL DEFAULT 100.0,   -- % de nb : toléré si dépassé
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_hb_type ON human_budget(type);

-- general_budget : limites de SÉCURITÉ globales (peuvent ne PAS être déclarées)
--   ex: req/min = 30000 (500 agents × 1/s) ; argent = 0 (force déclaration)
CREATE TABLE IF NOT EXISTS general_budget (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    type        TEXT NOT NULL,
    nb          REAL NOT NULL,
    provider    TEXT DEFAULT '-1',
    modele      TEXT DEFAULT '-1',
    project     TEXT DEFAULT '-1',
    team        TEXT DEFAULT '-1',
    agent       TEXT DEFAULT '-1',
    hard_limit  REAL DEFAULT 100.0,
    soft_limit  REAL DEFAULT 100.0,
    created_at  TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gb_type ON general_budget(type);

-- guess_bundle : cible + scores globaux (quotas seulement)
CREATE TABLE IF NOT EXISTS guess_bundle_quota (
    bundle_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_kind            TEXT NOT NULL,   -- provider | endpoint | adress
    target_ref             TEXT NOT NULL,
    score_precision_global REAL DEFAULT 0,
    score_coherence_global REAL DEFAULT 0,
    created_at             TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gbq_target ON guess_bundle_quota(target_kind, target_ref);

-- guess : fenêtres d'estimation de quota
CREATE TABLE IF NOT EXISTS guess_quota (
    guess_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id         INTEGER NOT NULL REFERENCES guess_bundle_quota(bundle_id) ON DELETE CASCADE,
    target_kind       TEXT NOT NULL,
    target_ref         TEXT NOT NULL,
    type_limite       TEXT NOT NULL,        -- budget_tag
    unit              TEXT NOT NULL,
    low_estimate      REAL DEFAULT 0,
    large_estimate    REAL DEFAULT 0,
    window_reset_low  INTEGER DEFAULT 0,
    window_reset_high INTEGER DEFAULT 0,
    coherence         REAL DEFAULT 0,
    precision         REAL DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gq_bundle ON guess_quota(bundle_id);

-- guess_bundle : cible + scores globaux (COÛTS)
CREATE TABLE IF NOT EXISTS guess_bundle_cost (
    bundle_id              INTEGER PRIMARY KEY AUTOINCREMENT,
    target_kind            TEXT NOT NULL,   -- provider | endpoint | adress
    target_ref             TEXT NOT NULL,
    score_precision_global REAL DEFAULT 0,
    score_coherence_global REAL DEFAULT 0,
    created_at             TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gbc_target ON guess_bundle_cost(target_kind, target_ref);

-- guess : estimation de COÛT (ratio unit_in→unit_out, variations)
CREATE TABLE IF NOT EXISTS guess_cost (
    guess_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    bundle_id         INTEGER NOT NULL REFERENCES guess_bundle_cost(bundle_id) ON DELETE CASCADE,
    target_kind       TEXT NOT NULL,
    target_ref         TEXT NOT NULL,
    type_limite       TEXT NOT NULL,        -- cost type
    unit_in           TEXT NOT NULL,        -- tok_in | tok_out | request | secondes
    unit_out          TEXT NOT NULL,        -- money | time | thinking_power
    low_estimate      REAL DEFAULT 0,       -- fourchette du RATIO unit_in→unit_out
    large_estimate    REAL DEFAULT 0,
    window_reset_low  INTEGER DEFAULT 0,
    window_reset_high INTEGER DEFAULT 0,
    depends_on_time   INTEGER DEFAULT 0,    -- 1 = varie selon heure/jour
    coherence         REAL DEFAULT 0,
    precision         REAL DEFAULT 0,
    created_at        TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_gc_bundle ON guess_cost(bundle_id);
