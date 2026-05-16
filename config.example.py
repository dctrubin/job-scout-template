# ─── Job Scout — Configuration Template ──────────────────────────────────────
#
# 1. Copy this file to config.py and fill in your settings.
# 2. Copy profile.example.md to profile.md and fill in your resume/targets.
#
# config.py and profile.md are gitignored — your personal info stays in your
# private fork and never appears in the template repo.

# ─── TUNING ───────────────────────────────────────────────────────────────────

HOURS_LOOKBACK      = 48   # how far back to look for jobs — used to limit scope on first run
SCORE_THRESHOLD     = 60   # min score (0–100) to push to Notion — start here, raise if too noisy
CONTACT_SCORE_BOOST = 10   # bonus points for companies where Has Contact = true in Notion
WATCH_SCORE_BOOST   = 5    # bonus for Priority=Watch companies in Notion companies DB
HIGH_SCORE_BOOST    = 10   # bonus for Priority=High companies in Notion companies DB

# ─── ROLE TITLES ──────────────────────────────────────────────────────────────
#
# Title keywords to match against job postings. Jobs pass the title filter if:
#   - The title contains any of these strings (case-insensitive), OR
#   - The title contains an ops/systems word AND a GTM domain qualifier (see below)
# No limit on the number of titles — no search API is used.

ROLE_TITLES = [
    "Your Role Title",
    "Another Role Title",
]

# ─── TITLE FILTER KEYWORDS ────────────────────────────────────────────────────
#
# Jobs are kept if the title contains a ROLE_TITLES match OR if it contains
# any _OPS_TERMS word AND any _DOMAIN_QUALS word (e.g. "Revenue Operations").
# Edit these sets if your target titles don't match either path.

_OPS_TERMS    = {"operations", "ops", "systems"}
_DOMAIN_QUALS = {"revenue", "customer success", "business", "gtm",
                 "go-to-market", "sales", "commercial", "growth"}

# ─── HARD FILTERS ─────────────────────────────────────────────────────────────
#
# Jobs matching any of these are dropped before scoring — saves Claude API calls.
# exclude_keywords: substrings matched against the job title (case-insensitive)
# exclude_company_slugs: ATS slugs of aggregator boards or companies to skip
# require_remote_or_austin: set False to remove the location gate entirely

HARD_FILTERS = {
    "exclude_keywords": [
        "staffing", "recruiting agency", "contract only", "hourly",
        "intern", "internship", "entry level", "junior",
    ],
    "exclude_company_slugs": {
        "jobgether", "remotar", "arc", "wellfound", "smartrecruiters",
    },
    "require_remote_or_austin": True,   # set False to remove location gate
}

# ─── SCORING RULES ────────────────────────────────────────────────────────────
#
# These rules are injected into the Claude prompt as hard overrides — they fire
# regardless of what the rest of the profile says. Use them to cap scores for
# patterns that are always disqualifying for you.
#
# Plain English works fine. Claude follows these as mandatory instructions.
# See SETUP.md → "Tuning Your Scores" for guidance on when to add rules here
# vs. updating profile.md.

SCORING_RULES = """1. LOCATION BLOCK: If this role requires on-site outside your target location
   with no remote option → cap score at 35, tier = "skip", apply_urgency = "low".
2. HARD REQUIREMENT MISS: For each qualification in a requirements section you clearly lack
   → deduct 20 points (max 2 deductions).
3. SALARY FLOOR: If the job listing states a base salary (not OTE) below $90,000
   → cap score at 30, tier = "skip", apply_urgency = "low".
"""
