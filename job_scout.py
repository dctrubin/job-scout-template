#!/usr/bin/env python3
"""
Job Scout - Search-based job discovery and AI scoring for Dakota Rubin

Discovery flow:
  1. Search Google (via Serper) for each ATS platform using role-title keywords
  2. Parse job URLs → extract company slug + job ID
  3. Fetch full job details from each platform's API (or scrape HTML for Rippling)
  4. Filter → score with Claude → push to Notion

No company list required. Covers Greenhouse, Ashby, Rippling, Lever automatically.
Workable and Breezy are available as per-company opt-ins via companies.json.
"""

import os
import json
import time
import logging
import re
import argparse
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Optional

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY       = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY          = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID      = os.environ.get("NOTION_DATABASE_ID", "")
NOTION_COMPANIES_DB_ID  = os.environ.get("NOTION_COMPANIES_DB_ID", "")
SERPER_API_KEY          = os.environ.get("SERPER_API_KEY", "")
PUSHOVER_USER_KEY       = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN      = os.environ.get("PUSHOVER_APP_TOKEN", "")

import pathlib

from config import (
    HOURS_LOOKBACK, SCORE_THRESHOLD, CONTACT_SCORE_BOOST,
    WATCH_SCORE_BOOST, HIGH_SCORE_BOOST, SERPER_RESULTS,
    ROLE_TITLES, _OPS_TERMS, _DOMAIN_QUALS,
    HARD_FILTERS, SCORING_RULES, PLATFORMS as _ENABLED_PLATFORMS,
)

_profile_path = pathlib.Path(__file__).parent / "profile.md"
  if not _profile_path.exists():                                                                                                                         
      print("Setup required: profile.md not found. Copy profile.example.md to profile.md and fill in your details.")
      raise SystemExit(0)
    )
CANDIDATE_PROFILE = _profile_path.read_text()

_DIR           = os.path.dirname(os.path.abspath(__file__))
SEEN_JOBS_FILE = os.path.join(_DIR, ".seen_jobs.json")

# ─── PLATFORM DEFINITIONS ─────────────────────────────────────────────────────
#
# Each entry drives:
#   - The `site:` prefix for the Google search
#   - A regex that parses (company_slug, job_id) from a matching URL
#   - A regex that extracts a human-readable company name from the search result title
#
# Priority order: Greenhouse → Ashby → Rippling → Lever

_PLATFORM_DEFS = [
    {
        "name": "greenhouse",
        "site": "boards.greenhouse.io",
        # https://boards.greenhouse.io/{slug}/jobs/{numeric-id}
        "url_re": re.compile(
            r"boards\.greenhouse\.io/([^/?#]+)/jobs/(\d+)", re.I
        ),
        # "Job Application for VP of RevOps at Stripe"
        "title_re": re.compile(r"Job Application for .+ at (.+)$", re.I),
    },
    {
        "name": "ashby",
        "site": "jobs.ashbyhq.com",
        # https://jobs.ashbyhq.com/{slug}/{uuid}
        "url_re": re.compile(
            r"jobs\.ashbyhq\.com/([^/?#]+)/([0-9a-f]{8}-[0-9a-f\-]{27,35})", re.I
        ),
        # "Revenue Operations Manager @ Runway Financial - Jobs"
        "title_re": re.compile(r".+ @ (.+?)(?:\s+-\s+(?:Jobs?|Careers?))?$", re.I),
    },
    {
        "name": "rippling",
        "site": "ats.rippling.com",
        # https://ats.rippling.com/{slug}/jobs/{uuid}
        # https://ats.rippling.com/en-US/{slug}/jobs/{uuid}  (locale prefix variant)
        "url_re": re.compile(
            r"ats\.rippling\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/?#]+)/jobs/([0-9a-f]{8}-[0-9a-f\-]{27,35})",
            re.I,
        ),
        # "Director, Revenue Operations | PDQ Careers"
        "title_re": re.compile(r"\|\s+(.+?)(?:\s+(?:Careers?|Jobs?|Career\s+Site|Current\s+Openings?|Job\s+[Oo]penings?))?\s*$", re.I),
    },
    {
        "name": "lever",
        "site": "jobs.lever.co",
        # https://jobs.lever.co/{slug}/{uuid}
        "url_re": re.compile(
            r"jobs\.lever\.co/([^/?#]+)/([0-9a-f]{8}-[0-9a-f\-]{27,35})", re.I
        ),
        # "Stripe - Director of Revenue Operations"
        "title_re": re.compile(r"^(.+?)\s+-\s+.+$", re.I),
    },
    {
        "name": "workday",
        "site": "myworkdayjobs.com",
        # https://{company}.wd1.myworkdayjobs.com/External/job/Remote/Director-RevOps_R-123
        "url_re": re.compile(
            r"([a-z0-9-]+)\.wd\d+\.myworkdayjobs\.com/(?:[^/]+/)*job/[^/]+/([^/?#]+)", re.I
        ),
        # Company name comes reliably from the subdomain slug — no title_re needed
    },
]

# Filter to only the platforms enabled in config.py
_enabled_set = set(_ENABLED_PLATFORMS)
PLATFORMS = [p for p in _PLATFORM_DEFS if p["name"] in _enabled_set]

# These Rippling title fragments are generic (not company names)
_RIPPLING_GENERIC = {"current openings", "job openings", "careers", "jobs", "career site", "openings"}

# ─── UTILITIES ────────────────────────────────────────────────────────────────

def strip_html(text: str) -> str:
    if not text:
        return ""
    try:
        return BeautifulSoup(text, "lxml").get_text(separator=" ", strip=True)
    except Exception:
        return re.sub(r"<[^>]+>", " ", text).strip()


def parse_iso_date(s: str) -> Optional[datetime]:
    if not s:
        return None
    s = s.strip()
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z",
                "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            dt = datetime.strptime(s[:26], fmt)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def slug_to_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").title()

# ─── COMPANIES  (Notion DB preferred, companies.json fallback) ────────────────

def load_companies_from_notion() -> list[dict]:
    """Read companies from the Notion companies database."""
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    companies = []
    cursor = None
    while True:
        body = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor
        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_COMPANIES_DB_ID}/query",
                headers=headers,
                json=body,
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to load companies from Notion: {e}")
            return []

        for page in data.get("results", []):
            props = page.get("properties", {})

            def txt(key):
                p = props.get(key, {})
                items = p.get("rich_text") or p.get("title") or []
                return items[0]["plain_text"].strip() if items else ""

            def chk(key):
                return props.get(key, {}).get("checkbox", False)

            def sel(key):
                s = props.get(key, {}).get("select") or {}
                return s.get("name", "")

            def url_val(key):
                return props.get(key, {}).get("url") or ""

            slug     = txt("Slug")
            ats      = sel("ATS").lower()
            priority = sel("Priority")   # "Normal", "Watch", "High"

            if not slug and not url_val("Job Board URL"):
                continue  # nothing actionable

            companies.append({
                "slug":          slug,
                "ats":           ats,
                "name":          txt("Name"),
                "has_contact":   chk("Has Contact"),
                "priority":      priority,
                "job_board_url": url_val("Job Board URL"),
                "contact_name":  txt("Contact Name"),
                "contact_role":  txt("Contact Role"),
                "relationship":  txt("Relationship"),
                "notes":         txt("Notes"),
            })

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")

    logger.info(f"Loaded {len(companies)} companies from Notion")
    return companies


def load_companies() -> list[dict]:
    """Load companies from the Notion companies database."""
    if not (NOTION_COMPANIES_DB_ID and NOTION_API_KEY):
        logger.warning("NOTION_COMPANIES_DB_ID not set — no company boosts or watch list")
        return []
    return load_companies_from_notion()

# ─── SERPER SEARCH ────────────────────────────────────────────────────────────

def search_platform(platform: dict) -> list[dict]:
    """
    Search Google (via Serper) for jobs on one ATS platform.
    Runs one query per role title and merges results (up to 70 per platform).
    Returns deduplicated raw Serper organic results.
    """
    if not SERPER_API_KEY:
        logger.error("SERPER_API_KEY not set — cannot run search-based discovery")
        return []

    logger.info(f"Searching {platform['name']} ({len(ROLE_TITLES)} title queries)...")

    all_results: list[dict] = []
    seen_links: set[str]   = set()

    for title in ROLE_TITLES:
        query = f'site:{platform["site"]} "{title}"'
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": SERPER_API_KEY, "Content-Type": "application/json"},
                json={"q": query, "num": SERPER_RESULTS},
                timeout=15,
            )
            resp.raise_for_status()
            for r in resp.json().get("organic", []):
                link = r.get("link", "")
                if link and link not in seen_links:
                    seen_links.add(link)
                    all_results.append(r)
            time.sleep(0.5)   # be polite between Serper calls
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else ""
            logger.error(f"Serper error ({platform['name']}, '{title}'): {e} — {body}")
        except Exception as e:
            logger.error(f"Serper error ({platform['name']}, '{title}'): {e}")

    logger.info(f"  {platform['name']}: {len(all_results)} unique search results")
    return all_results

# ─── URL PARSING ──────────────────────────────────────────────────────────────

def parse_search_results(results: list[dict], platform: dict) -> list[dict]:
    """
    Parse Serper results into structured job stubs.
    Returns [{"company_slug", "job_id", "url", "company_name"}], deduplicated.
    """
    seen = set()
    parsed = []
    url_re   = platform["url_re"]
    title_re = platform.get("title_re")
    pname    = platform["name"]

    for r in results:
        url = r.get("link", "")
        m   = url_re.search(url)
        if not m:
            continue

        # group(1) may be None for Wellfound /jobs/i/ URLs (no company in path)
        slug    = (m.group(1) or "").lower() or f"{pname}-job"
        job_id  = m.group(2)
        key = (slug, job_id)
        if key in seen:
            continue
        seen.add(key)

        # Extract company name from search result title
        company_name = ""
        search_title = r.get("title", "")
        if title_re and search_title:
            tm = title_re.search(search_title)
            if tm:
                candidate = tm.group(1).strip()
                # For Rippling, reject generic suffix-only matches
                if pname == "rippling" and candidate.lower() in _RIPPLING_GENERIC:
                    candidate = ""
                company_name = candidate

        if not company_name:
            company_name = slug_to_name(slug)

        parsed.append({
            "company_slug": slug,
            "job_id":       job_id,
            "url":          url,
            "company_name": company_name,
        })

    logger.info(f"  {pname}: {len(parsed)} unique job URLs parsed")
    return parsed

# ─── GREENHOUSE ───────────────────────────────────────────────────────────────

def fetch_greenhouse_job(slug: str, job_id: str, company_name: str, has_contact: bool) -> Optional[dict]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            logger.debug(f"Greenhouse 404: {slug}/{job_id}")
            return None
        resp.raise_for_status()
        job = resp.json()
        return {
            "id":           f"gh_{job_id}",
            "title":        job.get("title", ""),
            "company_slug": slug,
            "company_name": company_name,
            "ats":          "greenhouse",
            "url":          job.get("absolute_url", f"https://boards.greenhouse.io/{slug}/jobs/{job_id}"),
            "location":     (job.get("location") or {}).get("name", ""),
            "posted_at":    job.get("updated_at", ""),   # Greenhouse uses updated_at
            "description":  strip_html(job.get("content", "")),
            "has_contact":  has_contact,
        }
    except Exception as e:
        logger.error(f"Greenhouse fetch error ({slug}/{job_id}): {e}")
        return None

# ─── ASHBY ────────────────────────────────────────────────────────────────────

def fetch_ashby_company_jobs(
    slug: str, job_ids: set, company_name: str, has_contact: bool
) -> list[dict]:
    """
    Fetch the full Ashby job board for one company, return only jobs whose
    UUID appears in job_ids. One API call per company (batched).
    """
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data     = resp.json()
        org_name = (data.get("organization") or {}).get("name") or company_name
        results  = []
        for job in data.get("jobs", []):
            if job.get("id") not in job_ids:
                continue
            results.append({
                "id":           f"ash_{job['id']}",
                "title":        job.get("title", ""),
                "company_slug": slug,
                "company_name": org_name,
                "ats":          "ashby",
                "url":          job.get("jobUrl", f"https://jobs.ashbyhq.com/{slug}/{job['id']}"),
                "location":     job.get("locationName") or job.get("location") or "",
                "posted_at":    job.get("publishedDate", ""),
                "description":  strip_html(job.get("descriptionHtml") or job.get("description", "")),
                "has_contact":  has_contact,
            })
        return results
    except Exception as e:
        logger.error(f"Ashby fetch error ({slug}): {e}")
        return []

# ─── RIPPLING ─────────────────────────────────────────────────────────────────

def fetch_rippling_job(
    slug: str, job_id: str, job_url: str, company_name: str, has_contact: bool
) -> Optional[dict]:
    """
    Scrape a Rippling job page (server-side rendered HTML).
    Rippling has no public JSON API, so we parse the page directly.
    Falls back gracefully if Cloudflare or other protection blocks the request.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    title       = ""
    location    = ""
    description = ""
    posted_at   = ""

    try:
        resp = requests.get(job_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # 1. Try JSON-LD structured data (most reliable when present)
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if isinstance(data, dict) and "JobPosting" in str(data.get("@type", "")):
                    title       = data.get("title", "")
                    description = strip_html(data.get("description", ""))
                    posted_at   = data.get("datePosted", "")
                    loc         = data.get("jobLocation") or {}
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    location = (loc.get("address") or {}).get("addressLocality", "")
                    break
            except Exception:
                continue

        # 2. Fallback: scrape visible HTML
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)

        if not description:
            for sel in ("[data-testid='job-description']", ".job-description", "main", "article"):
                el = soup.select_one(sel)
                if el:
                    description = el.get_text(separator=" ", strip=True)[:3000]
                    break

        if not location:
            text = soup.get_text()
            m = re.search(r"(?:Location|location)[:\s]+([^\n|•]+)", text)
            if m:
                location = m.group(1).strip()

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 404:
            logger.debug(f"Rippling 404 (stale search result): {slug}/{job_id}")
        elif code in (403, 503):
            logger.warning(f"Rippling {slug}: Cloudflare blocked ({code}), description unavailable")
        else:
            logger.error(f"Rippling HTTP error ({slug}/{job_id}): {e}")
    except Exception as e:
        logger.error(f"Rippling scrape error ({slug}/{job_id}): {e}")

    return {
        "id":           f"rp_{job_id}",
        "title":        title or "",
        "company_slug": slug,
        "company_name": company_name,
        "ats":          "rippling",
        "url":          job_url,
        "location":     location,
        "posted_at":    posted_at,
        "description":  description,
        "has_contact":  has_contact,
    }

# ─── LEVER ────────────────────────────────────────────────────────────────────

def fetch_lever_job(slug: str, job_id: str, company_name: str, has_contact: bool) -> Optional[dict]:
    url = f"https://api.lever.co/v0/postings/{slug}/{job_id}?mode=json"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        job = resp.json()

        posted_at = ""
        if job.get("createdAt"):
            posted_at = datetime.fromtimestamp(
                job["createdAt"] / 1000, tz=timezone.utc
            ).isoformat()

        cats     = job.get("categories") or {}
        location = cats.get("location", "") or (
            cats.get("allLocations", [""])[0]
            if isinstance(cats.get("allLocations"), list)
            else ""
        )

        parts = [job.get("descriptionPlain", "")]
        for lst in job.get("lists", []):
            items = " ".join(lst.get("content", []))
            parts.append(f"{lst.get('text', '')}: {items}")

        return {
            "id":           f"lv_{job_id}",
            "title":        job.get("text", ""),
            "company_slug": slug,
            "company_name": company_name,
            "ats":          "lever",
            "url":          job.get("hostedUrl", f"https://jobs.lever.co/{slug}/{job_id}"),
            "location":     location,
            "posted_at":    posted_at,
            "description":  "\n".join(p for p in parts if p),
            "has_contact":  has_contact,
        }
    except Exception as e:
        logger.error(f"Lever fetch error ({slug}/{job_id}): {e}")
        return None

# ─── WELLFOUND ────────────────────────────────────────────────────────────────

def fetch_wellfound_job(
    slug: str, job_id: str, job_url: str, company_name: str, has_contact: bool
) -> Optional[dict]:
    """Scrape a Wellfound job page. Falls back gracefully if blocked."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    title = ""
    description = ""
    location = ""

    try:
        resp = requests.get(job_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Try JSON-LD first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if "JobPosting" in str(data.get("@type", "")):
                    title       = data.get("title", "")
                    description = strip_html(data.get("description", ""))
                    loc         = data.get("jobLocation") or {}
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    location = (loc.get("address") or {}).get("addressLocality", "")
                    break
            except Exception:
                continue

        # Fallback: HTML
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
        if not description:
            for sel in ["[class*='description']", "[class*='job-description']", "main"]:
                el = soup.select_one(sel)
                if el:
                    description = el.get_text(separator=" ", strip=True)[:6000]
                    break

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 404:
            logger.debug(f"Wellfound 404 (stale): {job_url}")
            return None
        logger.warning(f"Wellfound {code} for {slug}/{job_id}")
    except Exception as e:
        logger.error(f"Wellfound fetch error ({slug}/{job_id}): {e}")

    return {
        "id":           f"wf_{job_id}",
        "title":        title or "",
        "company_slug": slug,
        "company_name": company_name,
        "ats":          "wellfound",
        "url":          job_url,
        "location":     location,
        "posted_at":    "",
        "description":  description,
        "has_contact":  has_contact,
    }


# ─── WORKDAY ──────────────────────────────────────────────────────────────────

def fetch_workday_job(
    slug: str, job_id: str, job_url: str, company_name: str, has_contact: bool
) -> Optional[dict]:
    """Scrape a Workday job page (server-side rendered). Falls back gracefully."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    title = ""
    description = ""
    location = ""
    posted_at = ""

    try:
        resp = requests.get(job_url, headers=headers, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")

        # Try JSON-LD structured data first
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
                if "JobPosting" in str(data.get("@type", "")):
                    title       = data.get("title", "")
                    description = strip_html(data.get("description", ""))
                    posted_at   = data.get("datePosted", "")
                    loc         = data.get("jobLocation") or {}
                    if isinstance(loc, list):
                        loc = loc[0] if loc else {}
                    location = (loc.get("address") or {}).get("addressLocality", "")
                    break
            except Exception:
                continue

        # Fallback: HTML
        if not title:
            h1 = soup.find("h1")
            if h1:
                title = h1.get_text(strip=True)
            if not title:
                pt = soup.find("title")
                if pt:
                    title = pt.get_text(strip=True).split(" - ")[0].split(" | ")[0].strip()

        # Last-resort fallback: derive title from URL slug
        # e.g. "Director-Revenue-Operations_R-123456" → "Director Revenue Operations"
        if not title:
            path_slug = job_url.rstrip("/").rsplit("/", 1)[-1]
            path_slug = re.sub(r"[_-]?[Rr][_-]?\d+(?:[_-]\d+)*$", "", path_slug)  # _R0043379_1
            path_slug = re.sub(r"[_-][A-Za-z]{1,10}$", "", path_slug)               # _J or _EN locale
            path_slug = path_slug.strip("-_")
            title = path_slug.replace("-", " ").replace("_", " ").title()

        if not description:
            for sel in ["[data-automation-id='jobPostingDescription']",
                        ".job-description", "main", "article"]:
                el = soup.select_one(sel)
                if el:
                    description = el.get_text(separator=" ", strip=True)[:6000]
                    break

        if not location:
            text = soup.get_text()
            m = re.search(r"(?:Location|location)[:\s]+([^\n|•]+)", text)
            if m:
                location = m.group(1).strip()

    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else "?"
        if code == 404:
            logger.debug(f"Workday 404 (stale): {slug}/{job_id}")
            return None
        elif code in (403, 503):
            logger.warning(f"Workday {slug}: blocked ({code}), description unavailable")
        else:
            logger.error(f"Workday HTTP error ({slug}/{job_id}): {e}")
    except Exception as e:
        logger.error(f"Workday fetch error ({slug}/{job_id}): {e}")

    if not description:
        logger.debug(f"Workday: no description scraped for {slug}/{job_id} — skipping")
        return None

    return {
        "id":           f"wd_{job_id}",
        "title":        title or "",
        "company_slug": slug,
        "company_name": company_name,
        "ats":          "workday",
        "url":          job_url,
        "location":     location,
        "posted_at":    posted_at,
        "description":  description,
        "has_contact":  has_contact,
    }


# ─── WORKABLE (optional per-company) ─────────────────────────────────────────

def fetch_workable_jobs(slug: str, company_name: str, has_contact: bool) -> list[dict]:
    url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    try:
        resp = requests.post(
            url, json={"query": "", "location": [], "remote": True}, timeout=15
        )
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        jobs = []
        for job in resp.json().get("results", []):
            loc = job.get("location") or {}
            loc_parts = [loc.get("city", ""), loc.get("country_name", "")]
            if loc.get("telecommute") or job.get("remote"):
                loc_parts.insert(0, "Remote")
            shortcode = job.get("shortcode", job.get("id", ""))
            jobs.append({
                "id":           f"wk_{job.get('id', shortcode)}",
                "title":        job.get("title", ""),
                "company_slug": slug,
                "company_name": company_name,
                "ats":          "workable",
                "url":          job.get("url") or f"https://apply.workable.com/{slug}/j/{shortcode}",
                "location":     ", ".join(p for p in loc_parts if p),
                "posted_at":    job.get("created_date") or job.get("published_on", ""),
                "description":  strip_html(job.get("description", "")),
                "has_contact":  has_contact,
            })
        return jobs
    except Exception as e:
        logger.error(f"Workable error ({slug}): {e}")
        return []

# ─── BREEZY (optional per-company) ───────────────────────────────────────────

def fetch_breezy_jobs(slug: str, company_name: str, has_contact: bool) -> list[dict]:
    url = f"https://{slug}.breezy.hr/json"
    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code == 404:
            return []
        resp.raise_for_status()
        data = resp.json()
        raw  = data if isinstance(data, list) else data.get("positions", data.get("jobs", []))
        jobs = []
        for job in raw:
            loc = job.get("location") or {}
            loc_parts = [loc.get("city", ""), (loc.get("country") or {}).get("name", "")]
            if loc.get("is_remote") or loc.get("remote"):
                loc_parts.insert(0, "Remote")
            jobs.append({
                "id":           f"bz_{job.get('_id', job.get('id', ''))}",
                "title":        job.get("name", job.get("title", "")),
                "company_slug": slug,
                "company_name": company_name,
                "ats":          "breezy",
                "url":          job.get("url") or f"https://{slug}.breezy.hr/p/{job.get('_id', '')}",
                "location":     ", ".join(p for p in loc_parts if p),
                "posted_at":    job.get("published_date", ""),
                "description":  strip_html(job.get("description", "")),
                "has_contact":  has_contact,
            })
        return jobs
    except Exception as e:
        logger.error(f"Breezy error ({slug}): {e}")
        return []

# ─── FILTERING ────────────────────────────────────────────────────────────────


def is_relevant_title(title: str) -> bool:
    t = title.lower()
    # Fast path: direct match against known search phrases
    if any(rt.lower() in t for rt in ROLE_TITLES):
        return True
    # Flexible path: ops/systems word + GTM domain qualifier
    has_ops = any(kw in t for kw in _OPS_TERMS)
    has_qualifier = any(q in t for q in _DOMAIN_QUALS)
    return has_ops and has_qualifier


def is_within_lookback(posted_at: str, hours: int) -> bool:
    if not posted_at:
        return True  # No date = include (err on side of inclusion)
    dt = parse_iso_date(posted_at)
    if dt is None:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


def passes_hard_filters(job: dict) -> bool:
    title_lower = job.get("title", "").lower()

    # Exclude known job aggregators (not real employers)
    if job.get("company_slug", "") in HARD_FILTERS["exclude_company_slugs"]:
        return False

    # Exclude by title keyword
    for kw in HARD_FILTERS["exclude_keywords"]:
        if kw.lower() in title_lower:
            return False

    # Location gate
    if HARD_FILTERS["require_remote_or_austin"]:
        loc        = (job.get("location") or "").lower()
        desc_start = (job.get("description") or "")[:500].lower()

        # Reject any job that explicitly names a non-US country
        non_us_countries = (
            "canada", "united kingdom", "uk ", " uk,", "europe", "germany",
            "france", "australia", "india", "mexico", "netherlands", "ireland",
            "spain", "brazil", "singapore", "poland", "sweden",
            "latin america", "latam", "argentina", "colombia", "chile",
            "new zealand", "south africa", "philippines", "nigeria",
        )
        if any(c in loc for c in non_us_countries):
            return False
        # Catch location restrictions buried in the job description opening
        if any(c in desc_start for c in non_us_countries):
            return False

        location_ok = (
            not loc
            or "remote" in loc
            or "austin" in loc
            or "anywhere" in loc
            or "united states" in loc
            or loc in ("us", "usa", "u.s.", "u.s.a.")
            or "remote" in desc_start
        )
        if not location_ok:
            return False

    return True

# ─── SEEN JOBS CACHE ──────────────────────────────────────────────────────────

def load_seen() -> dict:
    if not os.path.exists(SEEN_JOBS_FILE):
        return {}
    try:
        with open(SEEN_JOBS_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_seen(seen: dict) -> None:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    pruned = {k: v for k, v in seen.items() if v >= cutoff}
    try:
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(pruned, f)
    except Exception as e:
        logger.error(f"Failed to save seen cache: {e}")

# ─── SCORING ──────────────────────────────────────────────────────────────────

def score_job(job: dict, has_contact: bool) -> dict:
    client      = Anthropic(api_key=ANTHROPIC_API_KEY)
    description = (job.get("description") or "")[:6000]

    prompt = f"""You are evaluating a job posting for a specific candidate. Score the fit and provide actionable insights.

CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

JOB POSTING:
Title: {job['title']}
Company: {job['company_name']}
Location: {job.get('location', '')}
ATS: {job['ats']}
URL: {job['url']}

Description:
{description}

has_contact: {has_contact}

MANDATORY SCORING RULES — apply these before general fit assessment:
{SCORING_RULES}

Respond ONLY with a JSON object (no markdown, no explanation):
{{
  "score": <integer 0-100, after applying all mandatory rules above>,
  "tier": <"A" if score>=80, "B" if 60-79, "C" if 40-59, "skip" if <40>,
  "title_match": <true/false>,
  "location_ok": <true/false>,
  "top_matches": [<3 specific reasons this is a strong match>],
  "gaps": [<up to 3 specific gaps or concerns, referencing mandatory rules where triggered>],
  "apply_urgency": <"high"|"medium"|"low">,
  "one_liner": <one sentence summary of fit, max 20 words>,
  "outreach_angle": <if has_contact true: one sentence on best angle for reaching out, else null>
}}

Scoring guide (before mandatory rule adjustments):
- 80-100: Strong match on scope, industry, and skills
- 60-79: Good match with minor gaps, worth applying
- 40-59: Partial match, missing key elements
- Below 40: Poor fit, skip"""

    try:
        response = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scoring = json.loads(raw.strip())
        if has_contact:
            scoring["score"] = min(100, scoring["score"] + CONTACT_SCORE_BOOST)
        scoring["has_contact"] = has_contact
        return scoring
    except Exception as e:
        logger.error(f"Scoring error for '{job.get('title', '?')}': {e}")
        return {
            "score": 50, "tier": "B", "title_match": True, "location_ok": True,
            "top_matches": ["Could not auto-score — review manually"],
            "gaps": ["Scoring failed"], "apply_urgency": "medium",
            "one_liner": "Manual review needed",
            "outreach_angle": None, "has_contact": has_contact,
        }

# ─── PUSHOVER ─────────────────────────────────────────────────────────────────

def send_pushover(job: dict, scoring: dict) -> None:
    """Send a Pushover push notification for a Tier A job. No-ops if keys not set."""
    if not PUSHOVER_USER_KEY or not PUSHOVER_APP_TOKEN:
        return
    company = job.get("company_name", job.get("company_slug", ""))
    title   = f"Tier A Job: {job['title']} @ {company}"
    message = (
        f"Score: {scoring['score']} | {scoring['apply_urgency'].upper()} urgency\n"
        f"{scoring.get('one_liner', '')}\n"
        f"{job.get('url', '')}"
    )
    try:
        requests.post(
            "https://api.pushover.net/1/messages.json",
            data={
                "token":    PUSHOVER_APP_TOKEN,
                "user":     PUSHOVER_USER_KEY,
                "title":    title,
                "message":  message,
                "priority": 1 if scoring.get("apply_urgency") == "high" else 0,
                "url":      job.get("url", ""),
                "url_title": "View Job",
            },
            timeout=10,
        )
        logger.info(f"Pushover sent for: {job['title']} @ {company}")
    except Exception as e:
        logger.error(f"Pushover error: {e}")


# ─── NOTION ───────────────────────────────────────────────────────────────────

def push_to_notion(job: dict, scoring: dict) -> bool:
    if not NOTION_API_KEY or not NOTION_DATABASE_ID:
        logger.warning("Notion credentials not set — skipping push")
        return False

    company = job.get("company_name", job.get("company_slug", ""))
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    properties = {
        "Name":          {"title":     [{"text": {"content": f"{job['title']} — {company}"}}]},
        "Score":         {"number":     scoring.get("score", 0)},
        "Tier":          {"select":    {"name": scoring.get("tier", "B")}},
        "Status":        {"select":    {"name": "To Review"}},
        "Company":       {"rich_text": [{"text": {"content": company}}]},
        "Location":      {"rich_text": [{"text": {"content": job.get("location", "")}}]},
        "ATS":           {"select":    {"name": job.get("ats", "other")}},
        "Has Contact":   {"checkbox":   scoring.get("has_contact", False)},
        "Apply Urgency": {"select":    {"name": scoring.get("apply_urgency", "medium")}},
        "One Liner":     {"rich_text": [{"text": {"content": scoring.get("one_liner", "")}}]},
        "Top Matches":   {"rich_text": [{"text": {"content": " | ".join(scoring.get("top_matches", []))}}]},
        "Gaps":          {"rich_text": [{"text": {"content": " | ".join(scoring.get("gaps", []))}}]},
        "URL":           {"url":        job.get("url") or None},
        "Posted At":     {"rich_text": [{"text": {"content": (job.get("posted_at") or "")[:10]}}]},
    }
    if scoring.get("outreach_angle"):
        properties["Outreach Angle"] = {
            "rich_text": [{"text": {"content": scoring["outreach_angle"]}}]
        }
    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=headers,
            json={"parent": {"database_id": NOTION_DATABASE_ID}, "properties": properties},
        )
        resp.raise_for_status()
        return True
    except requests.HTTPError as e:
        body = e.response.text if e.response is not None else ""
        logger.error(f"Notion push error ('{job.get('title', '?')}'): {e} — {body}")
        return False
    except Exception as e:
        logger.error(f"Notion push error ('{job.get('title', '?')}'): {e}")
        return False

# ─── DRY-RUN OUTPUT ───────────────────────────────────────────────────────────

def print_job(job: dict, scoring: dict) -> None:
    company = job.get("company_name", job.get("company_slug", ""))
    print(f"\n{'='*60}")
    print(f"TITLE: {job['title']}")
    print(f"COMPANY: {company} | HAS CONTACT: {job.get('has_contact', False)}")
    print(f"LOCATION: {job.get('location', '')}")
    print(f"SCORE: {scoring['score']} ({scoring['tier']}) | URGENCY: {scoring['apply_urgency']}")
    print(f"ONE LINER: {scoring['one_liner']}")
    print(f"MATCHES: {' | '.join(scoring.get('top_matches', []))}")
    print(f"GAPS: {' | '.join(scoring.get('gaps', []))}")
    if scoring.get("outreach_angle"):
        print(f"OUTREACH: {scoring['outreach_angle']}")
    print(f"URL: {job.get('url', '')}")
    print("="*60)

# ─── MAIN ORCHESTRATION ───────────────────────────────────────────────────────

def run_scout(dry_run: bool = False, hours_back: int = HOURS_LOOKBACK) -> list[dict]:
    logger.info(f"Job Scout starting — lookback={hours_back}h | dry_run={dry_run}")

    seen      = load_seen()
    companies = load_companies()
    contact_slugs  = {c["slug"] for c in companies if c.get("has_contact")}
    watch_slugs    = {c["slug"] for c in companies if c.get("priority") == "Watch"}
    high_slugs     = {c["slug"] for c in companies if c.get("priority") == "High"}
    # always_surface: contact, watch, and high priority companies all bypass score threshold
    always_surface = contact_slugs | watch_slugs | high_slugs

    all_jobs: list[dict] = []

    # ── Search-based discovery ─────────────────────────────────────────────────
    for platform in PLATFORMS:
        raw     = search_platform(platform)
        parsed  = parse_search_results(raw, platform)
        pname   = platform["name"]

        if pname == "greenhouse":
            for p in parsed:
                job_key = f"gh_{p['job_id']}"
                if job_key in seen:
                    continue
                hc  = p["company_slug"] in contact_slugs
                job = fetch_greenhouse_job(p["company_slug"], p["job_id"], p["company_name"], hc)
                if job:
                    all_jobs.append(job)
                time.sleep(0.3)

        elif pname == "ashby":
            # Batch by company: one board API call per unique company slug
            by_company:    defaultdict[str, set]  = defaultdict(set)
            company_names: dict[str, str]         = {}
            for p in parsed:
                job_key = f"ash_{p['job_id']}"
                if job_key not in seen:
                    by_company[p["company_slug"]].add(p["job_id"])
                    company_names[p["company_slug"]] = p["company_name"]
            for slug, job_ids in by_company.items():
                hc   = slug in contact_slugs
                jobs = fetch_ashby_company_jobs(slug, job_ids, company_names[slug], hc)
                all_jobs.extend(jobs)
                time.sleep(0.3)

        elif pname == "rippling":
            for p in parsed:
                job_key = f"rp_{p['job_id']}"
                if job_key in seen:
                    continue
                hc  = p["company_slug"] in contact_slugs
                job = fetch_rippling_job(
                    p["company_slug"], p["job_id"], p["url"], p["company_name"], hc
                )
                if job:
                    all_jobs.append(job)
                time.sleep(0.5)  # slightly longer — HTML scraping

        elif pname == "lever":
            for p in parsed:
                job_key = f"lv_{p['job_id']}"
                if job_key in seen:
                    continue
                hc  = p["company_slug"] in contact_slugs
                job = fetch_lever_job(p["company_slug"], p["job_id"], p["company_name"], hc)
                if job:
                    all_jobs.append(job)
                time.sleep(0.3)

        elif pname == "workday":
            for p in parsed:
                job_key = f"wd_{p['job_id']}"
                if job_key in seen:
                    continue
                hc  = p["company_slug"] in contact_slugs
                job = fetch_workday_job(
                    p["company_slug"], p["job_id"], p["url"], p["company_name"], hc
                )
                if job:
                    all_jobs.append(job)
                time.sleep(0.5)

    # ── Per-company opt-ins (Workable / Breezy from companies.json) ────────────
    for c in [x for x in companies if x.get("ats") == "workable"]:
        jobs = fetch_workable_jobs(
            c["slug"], c.get("name", slug_to_name(c["slug"])), c.get("has_contact", False)
        )
        all_jobs.extend(jobs)
        time.sleep(0.3)

    for c in [x for x in companies if x.get("ats") == "breezy"]:
        jobs = fetch_breezy_jobs(
            c["slug"], c.get("name", slug_to_name(c["slug"])), c.get("has_contact", False)
        )
        all_jobs.extend(jobs)
        time.sleep(0.3)

    logger.info(f"Total jobs fetched: {len(all_jobs)}")

    # ── Filter ─────────────────────────────────────────────────────────────────
    # Order: title match → already seen → hard filters → date (contact skips date)
    relevant: list[dict] = []
    for job in all_jobs:
        if not is_relevant_title(job.get("title", "")):
            continue
        if job["id"] in seen:
            continue
        if not passes_hard_filters(job):
            continue
        is_priority = job.get("company_slug", "") in always_surface
        if not is_priority and not is_within_lookback(job.get("posted_at", ""), hours_back):
            continue
        relevant.append(job)

    logger.info(f"Relevant new jobs after filtering: {len(relevant)}")

    if not relevant:
        logger.info("No new relevant jobs found.")
        save_seen(seen)
        return []

    # ── Score and push ─────────────────────────────────────────────────────────
    results: list[dict] = []
    skipped: list[dict] = []
    for job in relevant:
        company = job.get("company_name", job.get("company_slug", ""))
        logger.info(f"Scoring: {job.get('title', '?')} @ {company}...")
        slug    = job.get("company_slug", "")
        hc      = job.get("has_contact", False)
        scoring = score_job(job, hc)

        # Apply priority boosts (additive on top of contact boost already in score_job)
        if slug in high_slugs and slug not in contact_slugs:
            scoring["score"] = min(100, scoring["score"] + HIGH_SCORE_BOOST)
        elif slug in watch_slugs:
            scoring["score"] = min(100, scoring["score"] + WATCH_SCORE_BOOST)
        # Recalculate tier after boost
        s = scoring["score"]
        scoring["tier"] = "A" if s >= 80 else "B" if s >= 60 else "C" if s >= 40 else "skip"

        is_priority = slug in always_surface
        if scoring["score"] < SCORE_THRESHOLD and not is_priority:
            logger.info(f"  Score {scoring['score']} below threshold — skipping")
            seen[job["id"]] = datetime.now(timezone.utc).isoformat()
            if dry_run:
                skipped.append({"job": job, "scoring": scoring})
            time.sleep(1)
            continue

        logger.info(f"  Score: {scoring['score']} ({scoring['tier']}) — {scoring['one_liner']}")

        if dry_run:
            print_job(job, scoring)
        else:
            push_to_notion(job, scoring)
            if scoring.get("tier") == "A":
                send_pushover(job, scoring)

        seen[job["id"]] = datetime.now(timezone.utc).isoformat()
        results.append({"job": job, "scoring": scoring})
        time.sleep(1)

    # ── Dry-run: print skipped jobs summary ────────────────────────────────────
    if dry_run and skipped:
        print(f"\n{'='*60}")
        print(f"SKIPPED — below threshold ({len(skipped)} jobs):")
        print('='*60)
        for s in sorted(skipped, key=lambda x: x["scoring"]["score"], reverse=True):
            j, sc = s["job"], s["scoring"]
            co = j.get("company_name", j.get("company_slug", ""))
            print(f"  {sc['score']:3d} | {j.get('title','?')} @ {co}")
            top_gap = sc.get("gaps", [""])[0]
            if top_gap:
                print(f"       └─ {top_gap}")
        print('='*60)

    save_seen(seen)
    logger.info(f"Done. {len(results)} jobs processed above threshold.")
    return results

# ─── CLI ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Job Scout — search-based job discovery for Dakota Rubin"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print results to terminal; don't push to Notion",
    )
    parser.add_argument(
        "--hours", type=int, default=HOURS_LOOKBACK, metavar="N",
        help=f"Look back N hours (default: {HOURS_LOOKBACK})",
    )
    args = parser.parse_args()
    run_scout(dry_run=args.dry_run, hours_back=args.hours)
