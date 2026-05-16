-- ─── Job Scout — contacts + watchlist schema migration ────────────────────────
-- Run this in the Supabase SQL editor after supabase_jobs_schema.sql.
-- Safe to re-run — all statements use IF NOT EXISTS / ON CONFLICT / OR REPLACE.
--
-- What this does:
--   1. Creates company_watchlist table (replaces priority/notes on companies)
--   2. Creates contacts table (replaces has_contact/contact_name/contact_role on companies)
--   3. Migrates existing data from companies into the new tables
--   4. Replaces the company_job_stats and contact_research_priorities views
--      to join across the new tables instead of reading from companies columns
--
-- After verifying everything works, you can drop the old columns from companies:
--   ALTER TABLE companies DROP COLUMN IF EXISTS priority;
--   ALTER TABLE companies DROP COLUMN IF EXISTS has_contact;
--   ALTER TABLE companies DROP COLUMN IF EXISTS contact_name;
--   ALTER TABLE companies DROP COLUMN IF EXISTS contact_role;
--   ALTER TABLE companies DROP COLUMN IF EXISTS notes;
-- (Don't do this until job_scout.py is deployed and contacts are re-imported.)


-- ─── 1. Company watchlist ──────────────────────────────────────────────────────
-- Tracks companies you actively want to surface regardless of score or dead cache.
-- priority = 'watch' (+5 boost) or 'high' (+10 boost).

CREATE TABLE IF NOT EXISTS company_watchlist (
    id         UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    slug       TEXT        NOT NULL,
    ats        TEXT        NOT NULL,
    priority   TEXT        NOT NULL DEFAULT 'watch'
                           CHECK (priority IN ('watch', 'high')),
    notes      TEXT,
    added_at   TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (slug, ats)
);

CREATE INDEX IF NOT EXISTS idx_watchlist_slug_ats ON company_watchlist (slug, ats);

-- Migrate existing priority companies from the companies table.
-- Only inserts rows that don't already exist (safe to re-run).
INSERT INTO company_watchlist (slug, ats, priority, notes)
SELECT slug, ats, LOWER(priority), notes
FROM companies
WHERE LOWER(priority) IN ('watch', 'high')
  AND NOT EXISTS (
      SELECT 1 FROM company_watchlist cw
      WHERE cw.slug = companies.slug AND cw.ats = companies.ats
  );


-- ─── 2. Contacts ──────────────────────────────────────────────────────────────
-- Stores LinkedIn connections. linkedin_company is the raw company name from the
-- LinkedIn export and is the key used for fuzzy matching at scoring time.
--
-- slug + ats are populated when a match is found (either at import time via the
-- old linkedin_write.py flow, or progressively during scoring). NULL until matched.

CREATE TABLE IF NOT EXISTS contacts (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    linkedin_company TEXT        NOT NULL,
    contact_name     TEXT,
    contact_role     TEXT,
    linkedin_url     TEXT        UNIQUE,        -- NULL allowed; unique when present
    connected_on     DATE,
    slug             TEXT,                      -- populated when matched to a company
    ats              TEXT,
    source           TEXT        DEFAULT 'linkedin_export',
    created_at       TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_contacts_slug_ats  ON contacts (slug, ats);
CREATE INDEX IF NOT EXISTS idx_contacts_company   ON contacts (linkedin_company);

-- Migrate existing contacts from companies table.
-- Uses slug-derived name as linkedin_company since we don't have the original name.
-- These rows will be superseded when contacts are re-imported from the LinkedIn CSV.
INSERT INTO contacts (linkedin_company, contact_name, contact_role, slug, ats, source)
SELECT
    INITCAP(REPLACE(REPLACE(slug, '-', ' '), '_', ' ')) AS linkedin_company,
    contact_name,
    contact_role,
    slug,
    ats,
    'migrated_from_companies'
FROM companies
WHERE has_contact = TRUE
  AND contact_name IS NOT NULL
  AND contact_name <> ''
  AND NOT EXISTS (
      SELECT 1 FROM contacts ct
      WHERE ct.slug = companies.slug
        AND ct.ats  = companies.ats
        AND ct.source = 'migrated_from_companies'
  );


-- ─── 3. Updated views ─────────────────────────────────────────────────────────
-- These replace the versions in supabase_jobs_schema.sql.
-- Both now join to company_watchlist and contacts instead of reading from companies.

-- Company-level match stats (updated to use new tables)
CREATE OR REPLACE VIEW company_job_stats AS
SELECT
    c.slug,
    c.ats,
    w.priority,
    (COUNT(DISTINCT ct.id) > 0)                          AS has_contact,
    MIN(ct.contact_name)                                 AS contact_name,
    MIN(ct.contact_role)                                 AS contact_role,
    COUNT(j.id)                                         AS total_matches,
    COUNT(CASE WHEN j.tier = 'A' THEN 1 END)            AS tier_a,
    COUNT(CASE WHEN j.tier = 'B' THEN 1 END)            AS tier_b,
    COUNT(CASE WHEN j.tier = 'C' THEN 1 END)            AS tier_c,
    COUNT(CASE WHEN j.tier = 'skip' THEN 1 END)         AS tier_skip,
    ROUND(AVG(j.score)::NUMERIC, 1)                     AS avg_score,
    MAX(j.score)                                        AS best_score,
    MAX(j.scored_at)                                    AS last_seen,
    COUNT(CASE WHEN j.pushed_to_notion THEN 1 END)      AS notion_pushes
FROM companies c
LEFT JOIN company_watchlist w  ON w.slug = c.slug AND w.ats = c.ats
LEFT JOIN contacts ct          ON ct.slug = c.slug AND ct.ats = c.ats
JOIN  jobs j                   ON j.company_slug = c.slug AND j.ats = c.ats
GROUP BY c.slug, c.ats, w.priority;


-- Contact research priority list (updated — same logic, new source of has_contact)
CREATE OR REPLACE VIEW contact_research_priorities AS
SELECT
    slug,
    ats,
    priority,
    total_matches,
    tier_a,
    tier_b,
    avg_score,
    best_score,
    last_seen
FROM company_job_stats
WHERE has_contact = FALSE
  AND (tier_a > 0 OR (tier_b >= 2) OR (total_matches >= 3 AND avg_score >= 65))
ORDER BY tier_a DESC, tier_b DESC, avg_score DESC;


-- ─── Handy queries for the new tables ────────────────────────────────────────

-- View all watchlist companies:
-- SELECT slug, ats, priority, notes, added_at FROM company_watchlist ORDER BY priority DESC, slug;

-- View all contacts and their match status:
-- SELECT contact_name, linkedin_company, contact_role, slug, ats, source FROM contacts ORDER BY contact_name;

-- Contacts not yet matched to a slug (candidates for manual review or re-import):
-- SELECT contact_name, linkedin_company, contact_role, linkedin_url
-- FROM contacts WHERE slug IS NULL ORDER BY linkedin_company;

-- Add a company to watchlist:
-- INSERT INTO company_watchlist (slug, ats, priority, notes)
-- VALUES ('acme-corp', 'greenhouse', 'high', 'Strong ops team, talked to recruiter')
-- ON CONFLICT (slug, ats) DO UPDATE SET priority = EXCLUDED.priority, notes = EXCLUDED.notes;
