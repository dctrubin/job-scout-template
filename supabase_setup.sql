-- ════════════════════════════════════════════════════════════════════════════
-- Job Scout — complete database setup
--
-- Paste this entire file into the Supabase SQL Editor and click Run (once).
-- It creates every table and view Job Scout needs, plus the permission grants.
-- Safe to re-run — it will not overwrite existing data.
--
-- Part 1 (core: companies, jobs, dead_slugs, analytics views) runs first;
-- Part 2 (watchlist + contacts) runs after and rebuilds the shared views to
-- join the new tables. Order matters, so keep them in this order.
-- ════════════════════════════════════════════════════════════════════════════


-- ─── Job Scout — core schema ──────────────────────────────────────────────────
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Safe to run multiple times — all statements use IF NOT EXISTS / OR REPLACE.

-- ─── 1. Companies table ───────────────────────────────────────────────────────
-- Populated once by seed_companies.py (~26k rows). Refreshed monthly.

CREATE TABLE IF NOT EXISTS companies (
    id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug TEXT NOT NULL,
    ats  TEXT NOT NULL,
    UNIQUE (slug, ats)
);

CREATE INDEX IF NOT EXISTS idx_companies_slug_ats ON companies (slug, ats);
CREATE INDEX IF NOT EXISTS idx_companies_ats      ON companies (ats);


-- ─── 2. Dead slugs cache ──────────────────────────────────────────────────────
-- ATS slugs that returned 404/no-jobs — cached for 30 days to skip on re-poll.

CREATE TABLE IF NOT EXISTS dead_slugs (
    id        UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    ats       TEXT        NOT NULL,
    slug      TEXT        NOT NULL,
    cached_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (ats, slug)
);

CREATE INDEX IF NOT EXISTS idx_dead_slugs_ats_slug  ON dead_slugs (ats, slug);
CREATE INDEX IF NOT EXISTS idx_dead_slugs_cached_at ON dead_slugs (cached_at DESC);


-- ─── 3. Jobs table ────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS jobs (
    id               UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    job_id           TEXT        NOT NULL UNIQUE,   -- e.g. "gh_12345", "lv_uuid"
    ats              TEXT        NOT NULL,           -- "greenhouse", "lever", etc.
    company_slug     TEXT        NOT NULL,
    title            TEXT,
    location         TEXT,
    url              TEXT,
    score            INTEGER,
    tier             TEXT,                           -- "A", "B", "C", "skip"
    gaps             TEXT,                           -- pipe-joined gap strings
    top_matches      TEXT,                           -- pipe-joined match strings
    posted_at        DATE,
    scored_at        TIMESTAMPTZ DEFAULT NOW(),
    last_seen_at     TIMESTAMPTZ DEFAULT NOW(),
    pushed_to_notion BOOLEAN     DEFAULT FALSE
);
-- No FK on company_slug/ats — some slugs are inferred from custom job URL domains
-- and may differ slightly from the companies table slug.

CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs (company_slug, ats);
CREATE INDEX IF NOT EXISTS idx_jobs_tier      ON jobs (tier);
CREATE INDEX IF NOT EXISTS idx_jobs_scored_at ON jobs (scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_score     ON jobs (score DESC);

-- Salary (parsed from the posting) and Claude's label for the role's real
-- function. Added Oct 2026 — safe to re-run on an existing database.
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_min       INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_max       INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS salary_mid       INTEGER;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS team_function    TEXT;
ALTER TABLE jobs ADD COLUMN IF NOT EXISTS function_flagged BOOLEAN DEFAULT FALSE;


-- ─── 4. Company-level match stats ─────────────────────────────────────────────
-- Answers: which companies keep posting relevant roles?

-- Note: this is a baseline view. Part 2 below replaces it with a
-- richer version that joins company_watchlist and contacts.
-- DROP first (not CREATE OR REPLACE) so a re-run doesn't hit error 42P16:
-- once Part 2 has run, this view already exists with MORE columns, and
-- CREATE OR REPLACE is not allowed to drop columns from an existing view.
-- CASCADE also clears contact_research_priorities, which depends on this view.
DROP VIEW IF EXISTS company_job_stats CASCADE;
CREATE VIEW company_job_stats AS
SELECT
    c.slug,
    c.ats,
    COUNT(j.id)                                         AS total_matches,
    COUNT(CASE WHEN j.tier = 'A' THEN 1 END)            AS tier_a,
    COUNT(CASE WHEN j.tier = 'B' THEN 1 END)            AS tier_b,
    COUNT(CASE WHEN j.tier = 'C' THEN 1 END)            AS tier_c,
    COUNT(CASE WHEN j.tier = 'skip' THEN 1 END)         AS tier_skip,
    ROUND(AVG(j.score)::NUMERIC, 1)                     AS avg_score,
    MAX(j.score)                                        AS best_score,
    MAX(j.last_seen_at)                                 AS last_seen,
    COUNT(CASE WHEN j.pushed_to_notion THEN 1 END)      AS notion_pushes
FROM companies c
JOIN jobs j ON j.company_slug = c.slug AND j.ats = c.ats
GROUP BY c.slug, c.ats;


-- ─── 5. Contact research priority list ────────────────────────────────────────
-- Companies with Tier A/B jobs — best ROI for outreach research.
-- Note: Part 2 below replaces this with a version that also
-- filters has_contact = FALSE once the contacts table exists.

DROP VIEW IF EXISTS contact_research_priorities;
CREATE VIEW contact_research_priorities AS
SELECT
    slug,
    ats,
    total_matches,
    tier_a,
    tier_b,
    avg_score,
    best_score,
    last_seen
FROM company_job_stats
WHERE (tier_a > 0 OR (tier_b >= 2) OR (total_matches >= 3 AND avg_score >= 65))
ORDER BY tier_a DESC, tier_b DESC, avg_score DESC;


-- ─── 6. Weekly search activity ────────────────────────────────────────────────
-- Track search performance over time

CREATE OR REPLACE VIEW weekly_search_stats AS
SELECT
    DATE_TRUNC('week', scored_at)::DATE  AS week_of,
    COUNT(*)                             AS total_scored,
    COUNT(CASE WHEN pushed_to_notion THEN 1 END)    AS pushed_to_notion,
    COUNT(CASE WHEN tier = 'A' THEN 1 END)           AS tier_a,
    COUNT(CASE WHEN tier = 'B' THEN 1 END)           AS tier_b,
    COUNT(CASE WHEN tier = 'C' THEN 1 END)           AS tier_c,
    COUNT(CASE WHEN tier = 'skip' THEN 1 END)        AS skipped,
    ROUND(AVG(score)::NUMERIC, 1)                    AS avg_score,
    COUNT(DISTINCT company_slug)                     AS unique_companies
FROM jobs
GROUP BY 1
ORDER BY 1 DESC;


-- ─── 7. Score distribution by ATS platform ────────────────────────────────────

CREATE OR REPLACE VIEW platform_stats AS
SELECT
    ats,
    COUNT(*)                                          AS total_matches,
    COUNT(CASE WHEN tier = 'A' THEN 1 END)            AS tier_a,
    COUNT(CASE WHEN pushed_to_notion THEN 1 END)      AS pushed_to_notion,
    ROUND(AVG(score)::NUMERIC, 1)                     AS avg_score
FROM jobs
GROUP BY ats
ORDER BY total_matches DESC;


-- ─── Handy one-off queries ────────────────────────────────────────────────────

-- Which companies should I find a contact at?
-- SELECT * FROM contact_research_priorities LIMIT 20;

-- Which companies have the most Tier A matches?
-- SELECT slug, ats, tier_a, tier_b, total_matches, avg_score, last_seen
-- FROM company_job_stats
-- ORDER BY tier_a DESC, total_matches DESC
-- LIMIT 25;

-- How is my search performing week over week?
-- SELECT * FROM weekly_search_stats;

-- All Tier A jobs ever seen, newest first:
-- SELECT title, company_slug, score, url, scored_at
-- FROM jobs WHERE tier = 'A'
-- ORDER BY scored_at DESC;

-- Companies with repeated high-score appearances (worth following closely):
-- SELECT company_slug, ats, COUNT(*) AS appearances, AVG(score) AS avg_score
-- FROM jobs WHERE score >= 70
-- GROUP BY company_slug, ats
-- HAVING COUNT(*) >= 2
-- ORDER BY appearances DESC, avg_score DESC;


-- ─── 8. Data API grants ───────────────────────────────────────────────────────
-- Required for new Supabase projects (created after May 30, 2026).
-- Without these, PostgREST returns "42501" errors on all table access.
-- Safe to run on existing projects — grants are idempotent.

GRANT SELECT, INSERT, UPDATE, DELETE ON companies        TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON jobs             TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON dead_slugs       TO service_role;
GRANT SELECT ON company_job_stats            TO service_role;
GRANT SELECT ON contact_research_priorities  TO service_role;
GRANT SELECT ON weekly_search_stats          TO service_role;
GRANT SELECT ON platform_stats               TO service_role;


-- ════════════════════════════════════════════════════════════════════════════
-- PART 2 — contacts + watchlist
-- (runs after Part 1 above and rebuilds the shared views to join these tables)
-- ════════════════════════════════════════════════════════════════════════════
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

-- Migrate existing priority companies from the companies table (only if those
-- columns exist — skipped automatically on fresh installs).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'priority'
    ) THEN
        INSERT INTO company_watchlist (slug, ats, priority, notes)
        SELECT slug, ats, LOWER(priority), notes
        FROM companies
        WHERE LOWER(priority) IN ('watch', 'high')
          AND NOT EXISTS (
              SELECT 1 FROM company_watchlist cw
              WHERE cw.slug = companies.slug AND cw.ats = companies.ats
          );
    END IF;
END $$;


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

-- Migrate existing contacts from companies table (only if those columns exist —
-- skipped automatically on fresh installs).
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'companies' AND column_name = 'has_contact'
    ) THEN
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
    END IF;
END $$;


-- ─── 3. Updated views ─────────────────────────────────────────────────────────
-- These replace the baseline versions from Part 1 above.
-- Both now join to company_watchlist and contacts instead of reading from companies.
-- DROP first so we can freely change column names/order (CREATE OR REPLACE can't).

DROP VIEW IF EXISTS contact_research_priorities;
DROP VIEW IF EXISTS company_job_stats;

CREATE VIEW company_job_stats AS
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
    MAX(j.last_seen_at)                                 AS last_seen,
    COUNT(CASE WHEN j.pushed_to_notion THEN 1 END)      AS notion_pushes
FROM companies c
LEFT JOIN company_watchlist w  ON w.slug = c.slug AND w.ats = c.ats
LEFT JOIN contacts ct          ON ct.slug = c.slug AND ct.ats = c.ats
JOIN  jobs j                   ON j.company_slug = c.slug AND j.ats = c.ats
GROUP BY c.slug, c.ats, w.priority;


CREATE VIEW contact_research_priorities AS
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


-- ─── 4. Data API grants ───────────────────────────────────────────────────────
-- Required for new Supabase projects (created after May 30, 2026).
-- Safe to run on existing projects — grants are idempotent.

GRANT SELECT, INSERT, UPDATE, DELETE ON company_watchlist            TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON contacts                     TO service_role;
-- Re-grant views replaced by this file:
GRANT SELECT ON company_job_stats            TO service_role;
GRANT SELECT ON contact_research_priorities  TO service_role;


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


-- ════════════════════════════════════════════════════════════════════════════
-- Security — keep this section LAST
--
-- Turns on Row Level Security with no policies, which blocks Supabase's public
-- "anon" key from reading or changing anything. Job Scout uses the service_role
-- key, which bypasses RLS, so nothing else changes. The views are switched to
-- run with the caller's permissions so they can't be used to read around RLS.
-- Afterwards, Supabase → Advisors → Security Advisor should show no errors.
-- ════════════════════════════════════════════════════════════════════════════

ALTER TABLE companies         ENABLE ROW LEVEL SECURITY;
ALTER TABLE dead_slugs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE jobs              ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_watchlist ENABLE ROW LEVEL SECURITY;
ALTER TABLE contacts          ENABLE ROW LEVEL SECURITY;

ALTER VIEW IF EXISTS company_job_stats           SET (security_invoker = true);
ALTER VIEW IF EXISTS contact_research_priorities SET (security_invoker = true);
ALTER VIEW IF EXISTS weekly_search_stats         SET (security_invoker = true);
ALTER VIEW IF EXISTS platform_stats              SET (security_invoker = true);

REVOKE ALL ON companies, dead_slugs, jobs, company_watchlist, contacts FROM anon, authenticated;
