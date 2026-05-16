# Job Scout

Automated job discovery and AI scoring. Polls 6 company ATS platforms directly every day, scores each new posting against your profile using Claude, and pushes results to a Notion database twice daily via GitHub Actions.

**New user? Start here: [SETUP.md](SETUP.md)**

---

## How It Works

```
Once (setup):
  Run "Seed Companies" in GitHub Actions
  → pulls ~26,000 companies from Greenhouse, Lever, Ashby, BambooHR, Workable, Breezy
  → loads them into your Supabase database

Twice daily (automatic):
  For each company: poll their ATS for open jobs
  → filter by your target job titles
  → score each new match with Claude (reads full job description + your profile)
  → push jobs scoring 60+ to your Notion database
  → send Pushover alert for Tier A jobs (score 80+)
```

No search APIs. No scraping Google. Just direct polls to each ATS — free, fast, and real-time.

---

## Platforms Covered

| Platform | Companies |
|---|---|
| Greenhouse | ~8,000 |
| BambooHR | ~10,800 |
| Lever | ~4,400 |
| Ashby | ~2,800 |
| Workable | ~1,000+ |
| Breezy | ~500+ |

---

## Cost

| Service | Cost |
|---|---|
| Anthropic API (Claude) | ~$5–10/month |
| Supabase | Free |
| Notion | Free |
| GitHub Actions | Free |
| Pushover (optional) | $5 one-time |
| **Total** | **~$5–10/month** |

---

## What You Get in Notion

Every job that scores 60+ lands in your Notion database with:

- **Score** (0–100) and **Tier** (A = 80+, B = 60–79)
- **One Liner** — Claude's one-sentence fit summary
- **Top Matches** — the signals that drove the score up
- **Gaps** — what the job requires that you're missing
- **Outreach Angle** — suggested approach if you have a contact there
- Direct apply link, location, and posting date

---

## Files in This Repo

| File | Purpose |
|---|---|
| `job_scout.py` | Main engine — polls ATS platforms, scores jobs, pushes to Notion |
| `seed_companies.py` | One-time setup — loads ~26k companies into Supabase |
| `config.example.py` | Template for your personal settings (copy to `config.py`) |
| `profile.example.md` | Template for your candidate profile (goes in as a GitHub secret) |
| `supabase_jobs_schema.sql` | Database schema — run once in Supabase SQL editor |
| `supabase_contacts_schema.sql` | Contacts/watchlist schema — run once in Supabase SQL editor |
| `.github/workflows/job_scout.yml` | Automated daily runs |
| `.github/workflows/seed_companies.yml` | One-time seed trigger (run manually from GitHub) |
