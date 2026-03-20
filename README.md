# Job Scout

Search-based job discovery and AI scoring. Finds jobs across Greenhouse, Ashby, Lever, Rippling, and Workday, scores each against your profile using Claude, and pushes results to Notion twice daily via GitHub Actions.

**New user? Start here: [SETUP.md](SETUP.md)**

---

## How It Works

```
8am and 4pm CT daily (via GitHub Actions):

1. Build company list by scraping levergreen.dev (covers all Greenhouse + Lever companies)
2. Query Ashby network-wide, plus Workable/Breezy/Pinpoint per-company
3. Filter: title match → location → hard exclusions → already seen?
4. Score each passing job with Claude API
5. Push jobs scoring 60+ to Notion (contact companies always included regardless of score)
```

---

## Job Sources

| Platform | How companies are found | Notes |
|---|---|---|
| Greenhouse | levergreen.dev scrape (cached weekly) | ~10,000+ companies |
| Lever | levergreen.dev scrape (cached weekly) | ~5,000+ companies |
| Ashby | Network-wide API search | No company list needed |
| Workable | companies.json slugs | Add manually |
| Breezy HR | companies.json slugs | Add manually |
| Pinpoint | Network-wide if supported, per-company fallback | |

---

## Setup (one-time, ~45 min)

### 1. Install dependencies
```bash
pip install requests anthropic beautifulsoup4 lxml
```

### 2. Set environment variables
```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export NOTION_API_KEY="secret_..."
export NOTION_DATABASE_ID="your-database-id"
```

### 3. Set up Notion

**Create a Notion integration:**
1. Go to notion.so/my-integrations → "New integration" → name it "Job Scout"
2. Copy the Internal Integration Token → that's your `NOTION_API_KEY`

**Create a Notion database with these exact properties:**

| Property | Type | Options |
|---|---|---|
| Name | Title | — |
| Score | Number | — |
| Tier | Select | A, B, C, skip |
| Status | Select | To Review, Applied, Referral Requested, Phone Screen, Interview, Offer, Passed |
| Company | Text | — |
| Location | Text | — |
| ATS | Select | greenhouse, ashby, lever, workable, breezy, pinpoint |
| Has Contact | Checkbox | — |
| Apply Urgency | Select | high, medium, low |
| One Liner | Text | — |
| Top Matches | Text | — |
| Gaps | Text | — |
| Outreach Angle | Text | — |
| URL | URL | — |
| Posted At | Text | — |

3. Click "..." on the database → Connections → connect your Job Scout integration
4. Copy the database ID from the URL: `notion.so/workspace/THIS-LONG-ID?v=...`

### 4. Set up GitHub Actions (for automation)

1. Create a private GitHub repo called `job-scout`
2. Push all project files
3. Go to repo Settings → Secrets and Variables → Actions → add three secrets:
   - `ANTHROPIC_API_KEY`
   - `NOTION_API_KEY`
   - `NOTION_DATABASE_ID`

The `.github/workflows/job_scout.yml` file is already included — it runs at 8am and 4pm CT daily once pushed.

---

## Usage

```bash
# First run — test without touching Notion, look back 1 week
python job_scout.py --dry-run --hours=168

# Normal dry run (last 24 hours)
python job_scout.py --dry-run

# Full run — scores and pushes to Notion
python job_scout.py

# Look back further
python job_scout.py --hours=48
```

---

## Managing companies.json

`companies.json` serves two purposes only:

**1. Flag companies where you have contacts** (do this before first run):
```json
{"slug": "stripe", "ats": "greenhouse", "name": "Stripe", "has_contact": true}
```
Contact companies always appear in Notion regardless of score or posting date, get +10 score bonus, and include an outreach angle suggestion.

**2. Add companies on Workable or Breezy HR** (not covered by levergreen.dev):
```json
{"slug": "acme-corp", "ats": "workable", "name": "Acme Corp", "has_contact": false}
```

You do NOT need to manually add Greenhouse or Lever companies — levergreen.dev handles that automatically. The starter companies in companies.json are there primarily for contact-flagging.

**Finding the right slug:**
- Greenhouse: `boards.greenhouse.io/SLUG`
- Lever: `jobs.lever.co/SLUG`
- Ashby: `jobs.ashbyhq.com/SLUG`
- Workable: `apply.workable.com/SLUG`
- Breezy: `SLUG.breezy.hr`

---

## Tuning

In `job_scout.py`, adjust at the top:

| Variable | Default | What it does |
|---|---|---|
| `HOURS_LOOKBACK` | 24 | How far back to look for jobs |
| `SCORE_THRESHOLD` | 60 | Min score to push to Notion |
| `CONTACT_SCORE_BOOST` | 10 | Bonus points for contact companies |
| `ROLE_TITLES` | see file | Title keywords to match |
| `HARD_FILTERS` | see file | Location and exclusion keywords |

Too many irrelevant jobs → raise `SCORE_THRESHOLD` to 70, tighten `ROLE_TITLES`
Missing good jobs → lower `SCORE_THRESHOLD`, expand `ROLE_TITLES`, add companies

---

## Roadmap

**V2:**
- [ ] Auto-draft "why I'm a fit" blurb per Tier A role
- [ ] Auto-draft LinkedIn outreach message for contact companies
- [ ] Pushover push notification for Tier A roles (instant mobile alert, $5 one-time)
- [ ] Daily email digest as HTML summary
- [ ] Google Custom Search layer for Wellfound, Workday, Rippling

**V3:**
- [ ] Weekly analytics: which role types/companies convert to conversations
- [ ] Application status tracking to tune scoring over time
- [ ] Portfolio landing page signaling AI fluency

---

## Costs

| Service | Cost |
|---|---|
| Claude API (Sonnet) | ~$0.003/job → ~$9/month |
| All job board APIs | Free |
| Notion API | Free |
| GitHub Actions | Free |
| levergreen.dev scraping | Free |
| **Total** | **~$9-15/month** |

---

## Troubleshooting

**"No companies loaded"** → Check companies.json is valid: `python -m json.tool companies.json`

**"Notion push error"** → Verify all database properties exist and the integration is connected to the database (not just the workspace).

**GitHub Actions not running** → Check the Actions tab in your repo. Verify Actions are enabled and secrets are set correctly.

**Too many irrelevant results** → Raise `SCORE_THRESHOLD` to 70 in job_scout.py.

**Missing expected jobs** → Lower `SCORE_THRESHOLD`, or add the specific company to companies.json with the right ATS slug.
