-- ============================================================
-- BUFFER.DB — tampon des opérations externes (domaine `buffer`).
-- Spec : docs/local_catalogue_spec.md §5.
--
-- PRINCIPE :
--   - Un importeur externe (ex. script models.dev) dépose N ops 'pending'
--     en UNE écriture batch — jamais d'upsert individuel sur le catalogue.
--   - Le consumer (buffer/write.process) applique le batch dans x_data du
--     domaine local (token write_catalogue) en mini-batchs → 'applied'/
--     'error' (jamais de blocage : les erreurs sont marquées, le batch
--     continue).
--   - Modifs locales vers l'extérieur → direction 'out' (export distant
--     futur).
--   - Les ops 'applied' sont conservées (audit) ; requête par
--     (external_tag, status) pour rejouer les erreurs.
-- ============================================================

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS buffer_op (
    op_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    direction     TEXT NOT NULL DEFAULT 'in'
                  CHECK(direction IN ('in','out')),
    domain        TEXT NOT NULL,
    op            TEXT NOT NULL
                  CHECK(op IN ('add','modify','delete','refresh')),
    payload_json  TEXT NOT NULL DEFAULT '{}',
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending','applied','error','cancelled')),
    error         TEXT DEFAULT '',
    external_tag  TEXT DEFAULT '',
    ref_external  TEXT DEFAULT '',
    created_at    TEXT DEFAULT (datetime('now')),
    applied_at    TEXT DEFAULT NULL
);
CREATE INDEX IF NOT EXISTS idx_buf_status ON buffer_op(status, domain);
CREATE INDEX IF NOT EXISTS idx_buf_ext ON buffer_op(external_tag, status);