# ─── Job Scout — Configuration Template ──────────────────────────────────────
#
# 1. Copy this file to config.py and fill in your settings.
# 2. Copy profile.example.md to profile.md and fill in your resume/targets.
#
# Following SETUP.md? You create config.py in your PRIVATE repo on GitHub and
# commit it there (GitHub Actions needs it to run). It holds search settings
# only — never put API keys in it; those go in GitHub Secrets. Your profile goes
# in the CANDIDATE_PROFILE secret, not in a file.
#
# ─── HOW FILTERING WORKS (read this first) ────────────────────────────────────
#
# Job Scout filters in two stages, and you have to set up BOTH:
#
#   1. TITLE FILTER (this file: ROLE_TITLES + the keyword sets + HARD_FILTERS)
#      A cheap keyword gate that runs BEFORE any AI. It decides which jobs are
#      even LOOKED AT. If a job title doesn't pass this gate, Claude never sees
#      it and it can never reach Notion — no matter how good a fit it is.
#
#   2. SCORING (profile.md + SCORING_RULES below)
#      Claude reads profile.md and scores the jobs that PASSED the title filter.
#      This decides how GOOD each surviving job is.
#
# The #1 setup mistake: writing profile.md for your field but leaving the title
# keywords below on their defaults (which are tuned for revenue/sales operations
# roles). If you do that, the filter keeps surfacing ops jobs that don't match
# your profile, and your real target titles never get through. The keywords in
# this file and the targets in profile.md MUST describe the same kind of role.

# ─── TUNING ───────────────────────────────────────────────────────────────────

HOURS_LOOKBACK  = 48   # how far back to look for jobs — 48h accounts for indexing lag
SCORE_THRESHOLD = 60   # min score (0–100) to push to Notion — start here, raise if too noisy

# Score boosts for companies/contacts you've flagged in your Supabase watchlist
# (see SETUP.md). All three are required by the engine — set a value to 0 to
# disable a boost, but don't delete or comment one out (that crashes the run).
WATCH_SCORE_BOOST   = 5    # bonus for priority='watch' companies
HIGH_SCORE_BOOST    = 10   # bonus for priority='high' companies
CONTACT_SCORE_BOOST = 10   # bonus when you have a contact at the company
                           # (only matters once you've imported LinkedIn contacts;
                           #  harmless to leave at 10 — or set to 0 — until then)

# ─── CLAUDE MODEL (optional) ──────────────────────────────────────────────────
#
# Which Claude model scores your jobs, and how hard it thinks. The defaults are a
# good balance of quality and cost — you can leave these alone.
#   CLAUDE_EFFORT: "low" (cheapest, fine for most), "medium", or "high"
CLAUDE_MODEL  = "claude-sonnet-5-5"
CLAUDE_EFFORT = "low"

# ─── TITLE FILTER: ROLE TITLES + KEYWORDS ─────────────────────────────────────
#
# These settings ARE the title gate. A job title passes if EITHER:
#   (A) it contains any string in ROLE_TITLES (case-insensitive substring), OR
#   (B) it contains any _TITLE_TERMS word AND any _TITLE_QUALIFIERS word.
#
# There's no search API and no per-title cost, so list as many as you like.
#
# ── Path A: ROLE_TITLES ──
# Exact phrases you'd be happy to see in a title. Plain substring match, so
# "Product Manager" also catches "Senior Product Manager". Replace the examples
# below with YOUR target titles — the placeholders match nothing on their own.

ROLE_TITLES = [
    # --- EXAMPLES (revenue/sales operations). Delete these and add your own. ---
    "Revenue Operations",
    "RevOps",
    "Sales Operations",
    "Business Operations",
    "GTM Operations",
    "Strategy & Operations",
    "Chief of Staff",
]

# ── Path B: compound match (_TITLE_TERMS + _TITLE_QUALIFIERS) ──
# Catches titles your ROLE_TITLES list didn't anticipate, e.g.
# "Customer Success Systems & Operations Manager" or "Growth Strategy Lead".
# A title passes path B only if it contains a word from BOTH sets.
#
# IMPORTANT: these defaults are tuned for operations/revenue roles. If your field
# is different, CHANGE THEM to match, or path B will keep surfacing ops jobs you
# don't want. For example, a product manager might use:
#   _TITLE_TERMS      = {"manager", "lead", "owner"}
#   _TITLE_QUALIFIERS = {"product", "platform", "growth"}
#
# To turn path B OFF and rely only on ROLE_TITLES, leave either set empty
# (e.g. _TITLE_QUALIFIERS = set()).

_TITLE_TERMS      = {"operations", "ops", "systems", "strategy"}
_TITLE_QUALIFIERS = {"revenue", "customer success", "business", "gtm",
                     "go-to-market", "sales", "commercial", "growth"}

# ─── HARD FILTERS ─────────────────────────────────────────────────────────────
#
# Jobs matching any of these are dropped before scoring — saves Claude API calls.
# exclude_keywords: substrings matched against the job title (case-insensitive)
# exclude_company_slugs: ATS slugs of aggregator boards or companies to skip
# require_remote_or_city: the location gate (see note below)

# Your home city for the location gate, lowercase (e.g. "austin", "denver",
# "new york"). On-site jobs in this city pass the gate alongside remote/US-wide
# roles. Matched as a substring against each job's location.
TARGET_CITY = "austin"

HARD_FILTERS = {
    "exclude_keywords": [
        "staffing", "recruiting agency", "contract only", "hourly",
        "intern", "internship", "entry level", "junior",
    ],
    "exclude_company_slugs": {
        "jobgether", "remotar", "arc", "wellfound", "smartrecruiters",
    },
    # Location gate. When True, a job is kept only if it's remote/US-wide OR
    # located in TARGET_CITY (set above). Set to False to drop the location gate
    # entirely and accept jobs anywhere.
    "require_remote_or_city": True,
}

# ─── OPTIONAL GATES ───────────────────────────────────────────────────────────
#
# SALARY_FLOOR_BASE: if a posting states a base salary whose TOP is below this,
# it's dropped before Claude ever sees it (saves API cost). Postings with no
# salary always pass. 0 = off. Example: 90_000
SALARY_FLOOR_BASE = 0

# DISQUALIFY_FUNCTIONS: Claude labels each job's real function (e.g. "Finance").
# Jobs whose label contains any of these words are flagged in Supabase
# (jobs.function_flagged) so you can spot mis-titled roles. Flag only — nothing
# is dropped. Example: ["engineering", "finance", "legal", "recruiting"]
DISQUALIFY_FUNCTIONS = []

# ─── SCORING RULES ────────────────────────────────────────────────────────────
#
# These rules are injected into the Claude prompt as hard overrides — they fire
# regardless of what the rest of profile.md says. Use them to cap scores for
# patterns that are always disqualifying for you. Plain English works fine.
#
# Note: these affect SCORING only, not the title filter above. Write them in the
# second person ("you"/"your") so they read naturally against your profile.
# See SETUP.md → "Tuning Your Scores" for when to add a rule here vs. editing
# profile.md.

SCORING_RULES = """1. LOCATION BLOCK: If this role requires on-site work outside your target
   location with no remote option → cap score at 35, tier = "skip",
   apply_urgency = "low". Do NOT apply the cap if the role is listed as Remote,
   "City (Remote)", or hybrid with your city as an option.
2. HARD REQUIREMENT MISS: For each qualification in a requirements section you
   clearly lack → deduct 20 points (max 2 deductions).
3. SALARY FLOOR: If the listing states a base salary (not OTE) below $90,000
   → cap score at 30, tier = "skip", apply_urgency = "low". If no salary is
   listed, do NOT deduct.
"""
