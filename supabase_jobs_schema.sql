-- ─── Job Scout — jobs table + analytics views ────────────────────────────────
-- Run this in the Supabase SQL editor (Dashboard → SQL Editor → New query)
-- Safe to run multiple times — all statements use IF NOT EXISTS / OR REPLACE.

-- ─── 1. Jobs table ────────────────────────────────────────────────────────────

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
    pushed_to_notion BOOLEAN     DEFAULT FALSE,

    -- No FK constraint — company_slug/ats are used for joins but not enforced,
    -- since some slugs are inferred from custom job URL domains and may differ
    -- slightly from the companies table slug.
);

CREATE INDEX IF NOT EXISTS idx_jobs_company   ON jobs (company_slug, ats);
CREATE INDEX IF NOT EXISTS idx_jobs_tier      ON jobs (tier);
CREATE INDEX IF NOT EXISTS idx_jobs_scored_at ON jobs (scored_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_score     ON jobs (score DESC);


-- ─── 2. Company-level match stats ─────────────────────────────────────────────
-- Answers: which companies keep posting relevant roles?

CREATE OR REPLACE VIEW company_job_stats AS
SELECT
    c.slug,
    c.ats,
    c.priority,
    c.has_contact,
    c.contact_name,
    c.contact_role,
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
JOIN jobs j ON j.company_slug = c.slug AND j.ats = c.ats
GROUP BY c.slug, c.ats, c.priority, c.has_contact, c.contact_name, c.contact_role;


-- ─── 3. Contact research priority list ────────────────────────────────────────
-- Companies with Tier A/B jobs but no contact yet — best ROI for outreach research

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


-- ─── 4. Weekly search activity ────────────────────────────────────────────────
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


-- ─── 5. Score distribution by ATS platform ────────────────────────────────────

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
