# Job Scout — Setup Guide

Job Scout automatically finds and scores job postings twice a day and organizes results in a Notion database — no manual searching required.

**You will never need to run any code on your own computer.** Everything runs on GitHub's servers. Setup takes about 45–60 minutes and is mostly clicking through web UIs.

---

## Before you begin — accounts to create

You need accounts on 4 services (5 if you want phone alerts). Create all of them before starting the steps below — it's faster than switching back and forth mid-setup.

| Service | What it does | Cost |
|---|---|---|
| **GitHub** | Stores your code and runs it automatically twice a day | Free |
| **Supabase** | The database that tracks companies and jobs | Free |
| **Notion** | Where your scored jobs appear | Free |
| **Anthropic** | The AI (Claude) that reads each job posting and scores it | ~$5–10/month |
| **Pushover** *(optional)* | Phone notification when a great job (score 80+) is found | $5 one-time |

### Create your accounts now

1. **GitHub** — [github.com/signup](https://github.com/signup)
2. **Supabase** — [supabase.com](https://supabase.com) → Sign up (free)
3. **Notion** — [notion.so/signup](https://www.notion.so/signup) (free)
4. **Anthropic** — [console.anthropic.com](https://console.anthropic.com) → Sign up → go to **Billing** and add $10 in credits
5. **Pushover** *(optional)* — Install the Pushover app on your phone, then sign up at [pushover.net](https://pushover.net)

---

## Step 1: Create your own private copy of the repo

1. Make sure you're logged into GitHub
2. Go to [github.com/dctrubin/job-scout-template](https://github.com/dctrubin/job-scout-template)
3. Click the green **Use this template** button → **Create a new repository**
4. Name it anything you like (e.g. `job-scout`)
5. Set visibility to **Private**
6. Click **Create repository**

You'll land on your own copy at `github.com/YOUR-USERNAME/job-scout`. This is your repo — you'll be working here for the rest of setup.

> **Why private?** In later steps you'll add your resume and personal details. A private repo keeps that visible only to you.

---

## Step 2: Set up Supabase (your database)

Supabase is a free database service. Job Scout uses it to store the list of ~26,000 companies it monitors and every job it has seen. You'll create a project and run two setup scripts.

### 2a. Create a Supabase project

1. Go to [supabase.com](https://supabase.com) and log in
2. Click **New project**
3. Name it `job-scout` (or anything you like)
4. Choose a region close to you
5. Set a database password — save it somewhere, but you won't need it again
6. Click **Create new project** and wait ~1 minute for it to finish

### 2b. Run the database setup scripts

You'll run two SQL scripts that create the tables Job Scout needs. You do this in Supabase's built-in SQL editor — no coding required.

**First script (`supabase_jobs_schema.sql`):**

1. In your Supabase project, click **SQL Editor** in the left sidebar
2. Click **New query**
3. Go back to your GitHub repo → open `supabase_jobs_schema.sql` → click the **Copy raw file** button (clipboard icon)
4. Paste the contents into the Supabase SQL editor
5. Click **Run** (or press Cmd+Enter / Ctrl+Enter)
6. You should see "Success. No rows returned."

**Second script (`supabase_contacts_schema.sql`):**

Repeat the same steps for `supabase_contacts_schema.sql`.

> **What these scripts create:** A `companies` table (~26k rows after seeding), a `jobs` table where every scored job is stored, and a few helper views for analytics. Safe to run multiple times — they won't overwrite existing data.

### 2c. Get your Supabase credentials

You need two values from Supabase — save both for Step 7.

1. In your Supabase project, click **Project Settings** (gear icon) → **API**
2. Under **Project URL** — copy the URL that looks like `https://abcdefgh.supabase.co` → this is your **SUPABASE_URL**
3. Under **Project API keys** → copy the **service_role** key (the longer one, not `anon`) → this is your **SUPABASE_KEY**

> **Note:** Use the `service_role` key (not `anon`). It's the one that says "secret" next to it.

---

## Step 3: Set up your Notion workspace

Notion is where your scored jobs will appear. You'll duplicate a pre-built template that has all the right columns set up.

### 3a. Duplicate the jobs database template

Click the link below. It will open in Notion and show a **Duplicate** button in the top-right corner. Click it to add the database to your workspace.

**Jobs database:** [Open template →](https://dust-holiday-2a3.notion.site/329964e0b7f480748684d11ec9f1dde8?v=329964e0b7f48124bd1f000c7dc2cc4c&source=copy_link)

### 3b. Create a Notion integration

The integration is what allows Job Scout to write to your database.

1. Go to [notion.so/my-integrations](https://www.notion.so/my-integrations)
2. Click **New integration**
3. Name it **Job Scout**, leave all other settings as-is, click **Save**
4. You'll see an **Internal Integration Secret** starting with `ntn_` — copy it and save it for Step 7

### 3c. Connect the integration to your database

1. Open the Jobs database you duplicated in Step 3a
2. Click the **...** menu in the top-right corner of the page
3. Click **Connections** → find **Job Scout** → click **Connect**

### 3d. Save your database ID

1. Open the Jobs database in Notion
2. Look at the URL in your browser: `https://www.notion.so/your-name/THIS-LONG-STRING?v=...`
3. Copy the long string between the last `/` and the `?v=` — that's your **NOTION_DATABASE_ID**
4. Save it for Step 7

---

## Step 4: Get your API keys

### Anthropic API key

1. Go to [console.anthropic.com](https://console.anthropic.com) and log in
2. Click **API Keys** in the left sidebar → **Create Key**
3. Name it "Job Scout"
4. Copy the key — it starts with `sk-ant-`

> Save it now — you can't view it again after closing this screen.

### Pushover keys *(optional — for phone alerts on Tier A jobs)*

You need two values:

1. **User Key** — log in at [pushover.net](https://pushover.net). Your User Key is on the dashboard home page.
2. **App Token** — click **Your Applications** → **Create an Application** → name it "Job Scout" → click **Create Application** → copy the API Token on the next page.

---

## Step 5: Write your candidate profile

Your profile is the most important configuration. Job Scout sends it to Claude alongside every job posting — the quality of your profile directly determines the quality of your scores.

### 5a. Write your profile text

1. In your GitHub repo, open the file `profile.example.md`
2. Click the **Copy raw file** button (clipboard icon in the top right)
3. Open a text editor on your computer (TextEdit, Notepad, Notes — anything)
4. Paste the template and fill in your real information

**Tips for each section:**

| Section | Tips |
|---|---|
| **About You** | Your city, whether you want remote, whether you'd relocate |
| **Experience Summary** | 3–5 sentences: years of experience, what you do, the kinds of companies you've worked at |
| **Target Role Titles** | Exact titles you're targeting — keep these in sync with `config.py` |
| **Key Strengths & Achievements** | Bullet points with real numbers where you have them |
| **Technical Skills** | Every tool and software you actually know — Claude flags gaps if a job requires something not listed |
| **What You're Looking For** | Company stage, industry preferences, compensation target |
| **Deal Breakers** | Plain English — "no on-site outside Chicago", "not healthcare" — Claude lowers scores for these |
| **Skills I Don't Have** | Things you genuinely lack — prevents false high scores when a job has hard requirements you can't meet |

Keep your profile text open — you'll paste it into GitHub as a secret in the next step.

### 5b. Add your profile as a GitHub secret

Your profile is stored as a GitHub secret (not a file in your repo). This keeps your personal information private even if you ever accidentally make the repo public.

1. Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret**
3. Name: `CANDIDATE_PROFILE`
4. Value: paste your entire profile text (everything you wrote in Step 5a)
5. Click **Add secret**

---

## Step 6: Configure your job titles and filters

This file controls which job titles to look for, which locations to accept, and a few other filters.

1. In your GitHub repo, click **Add file** → **Create new file**
2. Name the file `config.py` (exactly — lowercase, with `.py`)
3. Go to `config.example.py` in your repo → copy its entire contents
4. Paste into the new file
5. Make these changes:

**`ROLE_TITLES`** — Replace the placeholder titles with the titles you're actually looking for:

```python
ROLE_TITLES = [
    "Revenue Operations Manager",
    "Director of Revenue Operations",
    "GTM Operations Manager",
    "Sales Operations Manager",
]
```

List any title that should automatically pass the filter. No limit on how many.

**`require_remote_or_austin`** — This defaults to `True`, which means only remote jobs or jobs in Austin, TX will pass. If you're in a different city or open to any location, change it:

```python
"require_remote_or_austin": False,   # removes the location gate entirely
```

6. When done, scroll down and click **Commit changes** → **Commit changes**

---

## Step 7: Add all secrets to GitHub

You've collected several keys and IDs across the previous steps. Add them all here.

1. Go to your GitHub repo → **Settings** → **Secrets and variables** → **Actions**
2. Click **New repository secret** for each row below:

| Secret name *(type exactly as shown)* | What it is | Where you got it |
|---|---|---|
| `ANTHROPIC_API_KEY` | `sk-ant-...` | Step 4 — Anthropic |
| `NOTION_API_KEY` | `ntn_...` | Step 3b — Notion integration |
| `NOTION_DATABASE_ID` | long ID string | Step 3d — Jobs database |
| `SUPABASE_URL` | `https://xxxx.supabase.co` | Step 2c |
| `SUPABASE_KEY` | service_role key | Step 2c |
| `CANDIDATE_PROFILE` | your full profile text | Step 5b — already added |
| `PUSHOVER_USER_KEY` | your user key | Step 4 — optional |
| `PUSHOVER_APP_TOKEN` | your app token | Step 4 — optional |

> If you skipped Pushover, leave the last two out — everything works fine without them, you just won't get phone alerts.

After adding all secrets, you should see 6–8 secrets listed on this page.

---

## Step 8: Seed your companies database

This loads ~26,000 companies into your Supabase database. You only ever need to do this once (and optionally again every few months to pick up new companies). It runs on GitHub's servers — nothing to install on your computer.

1. Go to your GitHub repo → click the **Actions** tab
2. In the left sidebar, click **Seed Companies**
3. Click **Run workflow** → **Run workflow** (the green button)
4. The workflow will appear in the list — click it to watch progress
5. Wait for it to finish with a green checkmark (takes 5–10 minutes)

**What you should see in the logs:** Lines like `Greenhouse: 8234 companies upserted`, `BambooHR: 10821 companies upserted`, etc.

If it fails, check that `SUPABASE_URL` and `SUPABASE_KEY` are added correctly in Step 7.

---

## Step 9: Trigger your first run

The automation runs automatically at ~4:45am and ~3:52pm Central Time every day. But trigger it manually now to verify everything is working.

1. Go to your repo → **Actions** tab
2. In the left sidebar, click **Job Scout**
3. Click **Run workflow** → **Run workflow**
4. Click the run to watch progress — takes 5–20 minutes depending on how active the platforms are
5. When it finishes with a green checkmark, open your Notion Jobs database

**What you should see:**

- Jobs with scores from 0–100 — anything 60+ was pushed to Notion
- Each job has a Tier (A = 80+, B = 60–79), a one-liner fit summary, and top matches / gaps
- If any jobs scored 80+ and you set up Pushover, you'll get a phone notification

**If nothing appears in Notion after a successful run:**
Your `ROLE_TITLES` in `config.py` may not be matching any job titles on the platforms, or all matches scored below 60. Check the run logs — look for lines starting with `[TITLE MATCH]` to see what's passing the filter.

---

## Tuning your scores

After a few runs, some scores will feel off. Every job in Notion has a **"Gaps"** and **"Top Matches"** field — read those before changing anything. They show you exactly why the score landed where it did.

**Score too high** (a job you'd never take got a 70+):

- Update the relevant section of your profile to be clearer
- Or add a rule to `SCORING_RULES` in `config.py`:

  ```python
  SCORING_RULES = """1. LOCATION BLOCK: ...
  2. HARD REQUIREMENT MISS: ...
  3. SALARY FLOOR: ...
  4. INDUSTRY BLOCK: If the company is primarily in healthcare or government → deduct 25 points.
  """
  ```

  Plain English works — Claude reads these as mandatory instructions.

**Score too low** (a job you'd love got a 45):

- Add the missing skill to **Technical Skills** in your profile
- Add the unusual title to `ROLE_TITLES` in `config.py`
- Make sure your profile's **Deal Breakers** section isn't accidentally penalizing similar jobs

---

## Adjusting run times *(optional)*

The default schedule is ~4:45am and ~3:52pm Central Time. To change it:

1. In your repo, open `.github/workflows/job_scout.yml`
2. Click the pencil (Edit) icon
3. Find the two `cron:` lines near the top
4. Change the times using UTC (add 5 hours for Central, 4 hours in summer CDT)

For example, to run at 7am and 5pm Central (CDT, summer):
```yaml
- cron: '45 12 * * *'   # 7:45am CDT = 12:45 UTC
- cron: '52 22 * * *'   # 5:52pm CDT = 22:52 UTC
```

---

## Troubleshooting

**Run fails immediately** → Check that all secrets are added correctly (Step 7). Secret names are case-sensitive and must match exactly.

**"Supabase error" or similar database error** → Double-check `SUPABASE_URL` and `SUPABASE_KEY`. Make sure you used the `service_role` key, not `anon`.

**No jobs appear in Notion after a successful run** → Check the run logs for `[TITLE MATCH]` lines. If you see none, your `ROLE_TITLES` aren't matching any job titles. Try adding more general titles (e.g. "Operations Manager"), or check for typos.

**"Notion push error"** → Make sure you connected the Notion integration to your Jobs database (Step 3c). The integration must be connected individually to the database, not just the workspace.

**Seed step failed partway through** → Safe to re-run — the seed script uses upserts and won't create duplicates. Just trigger the Seed Companies workflow again.

**Jobs are repeating every run** → This usually means the Supabase connection isn't working and the dedup cache isn't being saved. Check your Supabase credentials.
