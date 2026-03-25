# Job Scout — Setup Guide

Job Scout automatically finds and scores job postings twice a day, then organizes results in a Notion database — no manual searching required. Follow this guide start to finish and you'll be live in about an hour.

---

## Before you begin — accounts and costs

You'll need to create accounts on 5 services. Here's exactly what each one does and what it costs:

| Service | What it does | Cost |
|---|---|---|
| **GitHub** | Stores the code and runs it automatically twice a day | Free |
| **Notion** | The database where your scored jobs appear | Free |
| **Anthropic (different than ClaudeAI Sub)** | The AI (Claude) that reads each job posting and scores it against your profile | ~$5–10/month (pay-as-you-go, no subscription) |
| **Serper.dev** | Searches Google to find new job postings | Free (2,500 searches/month included) |
| **Pushover** | Sends a phone notification when a great job (score 80+) is found | $5 one-time (optional but recommended) |

**Total expected cost: ~$5–10/month** (just the Anthropic API). Everything else is free or a one-time $5.

Create all five accounts before starting the setup steps — it's faster than switching back and forth.

### Create your accounts now

1. **GitHub** — [github.com/signup](https://github.com/signup) (free)
2. **Notion** — [notion.so/signup](https://www.notion.so/signup) (free)
3. **Anthropic** — [console.anthropic.com](https://console.anthropic.com) → sign up, then add $10 in credits under **Billing**
4. **Serper.dev** — [serper.dev](https://serper.dev) → sign up (free)
5. **Pushover** — Install the Pushover app on your phone, then sign up at [pushover.net](https://pushover.net) ($5 one-time in-app purchase to activate)

---

## Step 1: Create your own private copy of the repo

1. Make sure you're logged into GitHub
2. Go to [github.com/dctrubin/job-scout-template](https://github.com/dctrubin/job-scout-template)
3. Click the green **Use this template** button → **Create a new repository**
4. Name it whatever you like (e.g. `job-scout`)
5. Set visibility to **Private**
6. Click **Create repository**

You'll land on your own copy at `github.com/YOUR-USERNAME/job-scout`. This is where you'll work for the rest of setup.

**Why private?** In the next steps you'll add your resume and personal details to a file called `profile.md`. A private repo keeps that information visible only to you.

---

## Step 2: Set up your Notion workspace

Notion is where your scored jobs will appear. You'll duplicate a pre-built template that has all the right columns already set up.

### 2a. Duplicate the template databases

Click both links below. Each one will open in Notion and show a **Duplicate** button in the top-right corner. Click it to add the database to your workspace.

- **Jobs database** (where scored jobs land):
  https://dust-holiday-2a3.notion.site/329964e0b7f480748684d11ec9f1dde8?v=329964e0b7f48124bd1f000c7dc2cc4c&source=copy_link

- **Companies database** (optional — add specific companies to watch or boost):
  https://dust-holiday-2a3.notion.site/329964e0b7f48056b449d79f06680415?v=329964e0b7f48111bfe5000c76ce9b96&source=copy_link

### 2b. Create a Notion integration (connects the code to your databases)

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration**
3. Name it **Job Scout**, leave all other settings as-is, click **Save**
4. You'll see an **Internal Integration Secret** — it starts with `ntn_`. **Copy this and save it somewhere** — you'll need it in Step 6.

### 2c. Connect the integration to your databases

You need to do this for both databases you duplicated:

1. Open the **Jobs** database in Notion
2. Click the **...** menu in the top-right corner of the page
3. Click **Connections** → find **Job Scout** → click **Connect**
4. Repeat for the **Companies** database

### 2d. Save your database IDs

Each database has a unique ID you'll need later. Here's how to find it:

1. Open the Jobs database in Notion
2. Look at the URL in your browser — it looks like:
   `https://www.notion.so/your-name/THIS-LONG-STRING-OF-LETTERS?v=...`
3. Copy the long string between the last `/` and the `?v=`. That's your **Jobs DB ID**.
4. Repeat for the Companies database to get your **Companies DB ID**.

Save both IDs — you'll use them in Step 6.

---

## Step 3: Collect your API keys

An API key is like a password that lets the code access each service on your behalf. You'll collect one from each service and add them to GitHub in Step 6. Here's exactly where to find each one:

### Anthropic API key (for Claude — scores your jobs)

1. Go to [console.anthropic.com](https://console.anthropic.com) and log in
2. Click **API Keys** in the left sidebar
3. Click **Create Key**, give it a name like "Job Scout"
4. Copy the key — it starts with `sk-ant-`
5. **Save it now** — you can't view it again after closing this screen

### Notion API key (for reading/writing your databases)

You already created this in Step 2b. It starts with `ntn_`. Find it again at [notion.so/my-integrations](https://www.notion.so/my-integrations) → click your Job Scout integration.

### Serper API key (for searching Google)

1. Go to [serper.dev](https://serper.dev) and log in
2. Your API key is shown on the dashboard home page
3. Copy it

### Pushover keys (for phone notifications — skip if you skipped Pushover)

You need **two** values from Pushover:

1. **User Key** — log in at [pushover.net](https://pushover.net). Your User Key is shown on the dashboard home page. Copy it.
2. **App Token** — click **Your Applications** → **Create an Application/API Token** → name it "Job Scout" → click **Create Application** → copy the **API Token** shown on the next page.

---

## Step 4: Write your profile

This is the most important step. Job Scout sends your profile to Claude along with every job posting it finds. The quality of your profile determines the quality of your scores.

Your profile is a plain text file — no coding required.

1. In your forked GitHub repo, find the file called `profile.example.md`
2. Click on it, then click the **pencil icon** (Edit) in the top right
3. Select all the text and copy it
4. Click the back arrow to go back to the file list
5. Click **Add file → Create new file**
6. Name the file `profile.md`
7. Paste the template text and fill in your details (instructions are inside the template)
8. Click **Commit changes** → **Commit changes** (green button)

**What to write in each section:**

| Section | Tips |
|---|---|
| **About You** | Your city, whether you need remote, whether you'd relocate |
| **Experience Summary** | 3–5 sentences: years of experience, your functional area, the kinds of companies you've worked at |
| **Target Role Titles** | The exact titles you're searching for — be specific |
| **Key Strengths & Achievements** | Bullet points with real numbers where you have them |
| **Technical Skills** | Every tool and software you actually know — Claude flags gaps if a job requires something not listed |
| **What You're Looking For** | Company size/stage, industry, compensation target |
| **Deal Breakers** | Plain English — "no on-site outside Seattle", "not interested in healthcare" — Claude will lower scores when these appear |
| **Skills I Don't Have** | Things you genuinely lack — this prevents false high scores when jobs have hard requirements you can't meet |

---

## Step 5: Set up your config file

The config file controls which job titles to search for, which platforms to search, and a few scoring thresholds. Like the profile, you'll do this directly in GitHub.

1. In your repo, find `config.example.py` → click it → click **Edit (pencil icon)**
2. Select all, copy
3. Go back, click **Add file → Create new file**
4. Name it `config.py`
5. Paste and edit the values below, then commit

**The only things you need to change:**

**`ROLE_TITLES`** — Replace the placeholder titles with the actual job titles you're looking for. Use 6–8 titles.

```python
ROLE_TITLES = [
    "Revenue Operations Manager",
    "Director of Revenue Operations",
    "GTM Operations Manager",
    "Sales Operations Manager",
]
```

> **Why 8 max?** Each title triggers one Google search per platform per run. 8 titles × 5 platforms × 2 runs/day × 30 days = 2,400 searches — right at the free tier limit of 2,500.

**`HARD_FILTERS → require_remote_or_austin`** — If you're not in Austin and/or not looking for remote-only roles, change `True` to `False` to remove the location gate.

**Everything else** — Leave the defaults. You can tune them later once you see how the scores look.

---

## Step 6: Add your API keys to GitHub

GitHub Actions (the automation that runs your code) needs your API keys to do its job. You'll add them as "secrets" — GitHub encrypts them so they're never visible in your code.

1. Go to your forked repo on GitHub
2. Click **Settings** → **Secrets and variables** → **Actions**
3. Click **New repository secret** for each key below:

| Secret name (type exactly as shown) | Value | Where you got it |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Step 3 — Anthropic |
| `NOTION_API_KEY` | `ntn_...` | Step 2b — Notion integration |
| `NOTION_DATABASE_ID` | long ID string | Step 2d — Jobs DB ID |
| `NOTION_COMPANIES_DB_ID` | long ID string | Step 2d — Companies DB ID |
| `SERPER_API_KEY` | your key | Step 3 — Serper.dev dashboard |
| `PUSHOVER_USER_KEY` | your key | Step 3 — Pushover dashboard |
| `PUSHOVER_APP_TOKEN` | your token | Step 3 — Pushover application |

> If you skipped Pushover, leave the last two out — the tool works fine without them, you just won't get phone alerts.

---

## Step 7: Trigger your first run

The automation is set to run automatically at **8am and 4pm Central Time** every day. But let's trigger it manually now to make sure everything is working.

1. Go to your repo on GitHub → click the **Actions** tab
2. In the left sidebar, click **Job Scout**
3. Click the **Run workflow** dropdown → **Run workflow**
4. The run will appear in the list — click it to watch progress (takes 3–5 minutes)
5. When it finishes with a green checkmark, open your Notion Jobs database

**What you should see:**
- Jobs with scores from 0–100 — anything 60+ was pushed to Notion
- Each job has a **Tier** (A = 80+, B = 60–79), a one-liner summary, and a **Why** column with Claude's full reasoning
- If any jobs scored 80+, you'll get a Pushover notification on your phone (if set up)

**If the run fails (red X):** Click the failed run → click the failed step → read the error message. Common issues are listed at the bottom of this guide.

---

## Step 8: Adjust run times (optional)

The default schedule is 8am and 4pm Central Time. To change it:

1. In your repo, open `.github/workflows/job_scout.yml`
2. Click **Edit (pencil icon)**
3. Find the two `cron:` lines near the top — they look like `'45 13 * * *'`
4. Change the numbers using the table below

Cron times use **UTC** (Universal Time). To find your UTC time: take your local time and add the offset.

| Your timezone | Add to local time to get UTC |
|---|---|
| Eastern (summer / EDT) | +4 hours |
| Eastern (winter / EST) | +5 hours |
| Central (summer / CDT) | +5 hours |
| Central (winter / CST) | +6 hours |
| Mountain (summer / MDT) | +6 hours |
| Pacific (summer / PDT) | +7 hours |

**Example:** You're in Eastern time and want runs at 8am and 5pm.
- 8am EDT = 12:00 UTC → `cron: '45 12 * * *'`
- 5pm EDT = 21:00 UTC → `cron: '45 21 * * *'`

The format is `'MM HH * * *'` where MM = minutes and HH = hour (24h clock). The `:45` on minutes is intentional — GitHub's queue is less congested at non-round times.

---

## Tuning your scores

After your first few runs, some scores will feel off. Here's how to fix them — it takes about 5 minutes and the next run will be more accurate.

**Every job in Notion has a "Why" column** — Claude's written reasoning for the score. Always read this before changing anything. It tells you exactly why the score landed where it did.

### Score too high (a job you'd never take got a 70+)

Read the Why. Then:
- If Claude misunderstood your situation → update the relevant section of `profile.md`
- If it's a pattern that should always be penalized → open `config.py` and add a rule to `SCORING_RULES`:

```
4. INDUSTRY BLOCK: If the company is primarily in healthcare or government → deduct 25 points.
5. CONTRACT BLOCK: If the role is contract-only or 1099 → cap score at 20, tier = "skip".
```

Plain English works — Claude reads these as mandatory instructions.

### Score too low (a job you'd love got a 45)

Read the Why. Then:
- If Claude flagged a skill gap that isn't real → add that skill to `profile.md` under Technical Skills
- If Claude missed a relevant strength → beef up that section in `profile.md`
- If the title was unusual → add it to Target Role Titles in `profile.md`

---

## Troubleshooting

**Run fails immediately** → Check that all 7 secrets are added correctly in GitHub Settings. The secret names must match exactly (case-sensitive).

**No jobs appear in Notion after a successful run** → Your ROLE_TITLES may not be matching anything, or SCORE_THRESHOLD (default 60) is filtering everything out. Try running with a 1-week lookback by temporarily changing `HOURS_LOOKBACK = 168` in `config.py`.

**Notion push error in the logs** → Make sure you connected your Notion integration to both databases (Step 2c). The integration must be connected to each database individually.

**Workday jobs missing descriptions** → Normal — Workday uses JavaScript to render pages, which the scraper can't always access. Those jobs are skipped. Not much to do here.

**Rippling jobs failing intermittently** → Also normal — Rippling has Cloudflare protection that blocks scrapers unpredictably. If it's causing too many errors, remove `"rippling"` from PLATFORMS in `config.py`.
