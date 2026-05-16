#!/usr/bin/env python3
"""
Job Scout — Direct ATS polling and AI scoring for Dakota Rubin

Discovery flow:
  1. Load all companies from Supabase (38k companies, VIP flags included)
  2. Poll each company's ATS API in parallel for all open jobs
  3. Filter by title → check seen cache → hard filters
  4. Score new matches with Claude → push to Notion → Pushover alert (Tier A)

Covers Greenhouse, Lever, Ashby, Workable, BambooHR, Breezy.
No search API required. Operational data (seen jobs, dead slugs) persists in Supabase.
"""

import os
import json
import time
import logging
import re
import argparse
import pathlib
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Set, Tuple
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from bs4 import BeautifulSoup
from anthropic import Anthropic
from supabase import create_client, Client
from rapidfuzz import fuzz, process as rfuzz_process

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ─── CONFIG ───────────────────────────────────────────────────────────────────

ANTHROPIC_API_KEY  = os.environ.get("ANTHROPIC_API_KEY", "")
NOTION_API_KEY     = os.environ.get("NOTION_API_KEY", "")
NOTION_DATABASE_ID = os.environ.get("NOTION_DATABASE_ID", "")
PUSHOVER_USER_KEY  = os.environ.get("PUSHOVER_USER_KEY", "")
PUSHOVER_APP_TOKEN = os.environ.get("PUSHOVER_APP_TOKEN", "")
SUPABASE_URL       = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY       = os.environ.get("SUPABASE_KEY", "")

from config import (
    HOURS_LOOKBACK, SCORE_THRESHOLD, CONTACT_SCORE_BOOST,
    WATCH_SCORE_BOOST, HIGH_SCORE_BOOST,
    ROLE_TITLES, _OPS_TERMS, _DOMAIN_QUALS,
    HARD_FILTERS, SCORING_RULES,
)

_profile_path = pathlib.Path(__file__).parent / "profile.md"
if not _profile_path.exists():
    print("Setup required: profile.md not found. Copy profile.example.md to profile.md and fill in your details.")
    raise SystemExit(0)
CANDIDATE_PROFILE = _profile_path.read_text()

_DIR            = os.path.dirname(os.path.abspath(__file__))
SEEN_JOBS_FILE  = os.path.join(_DIR, ".seen_jobs.json")   # fallback if Supabase not configured
DEAD_SLUGS_FILE = os.path.join(_DIR, ".dead_slugs.json")  # fallback if Supabase not configured

# Parallel polling worker counts per platform
PLATFORM_WORKERS: Dict[str, int] = {
    "greenhouse": 10,
    "lever":      10,
    "ashby":       5,
    "bamboohr":    8,
    "workable":    8,
    "breezy":      5,
}

# ─── SUPABASE CLIENT ──────────────────────────────────────────────────────────

def _get_supabase() -> Optional[Client]:
    """Return Supabase client if credentials are configured, else None."""
    if SUPABASE_URL and SUPABASE_KEY:
        return create_client(SUPABASE_URL, SUPABASE_KEY)
    return None

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


_NORM_PUNCT     = re.compile(r"[^a-z0-9\s]")
_NORM_SPACES    = re.compile(r"\s+")
_NORM_SUFFIXES  = re.compile(
    r"\b(inc|llc|llp|ltd|corp|corporation|co|company|group|holdings|"
    r"international|worldwide|global|technologies|technology|tech|"
    r"solutions|services|consulting|partners|associates|ventures)\b",
    re.IGNORECASE,
)

def normalize_company(name: str) -> str:
    """Lowercase, strip legal suffixes and punctuation, collapse whitespace."""
    name = name.lower()
    name = _NORM_SUFFIXES.sub("", name)
    name = _NORM_PUNCT.sub(" ", name)
    name = _NORM_SPACES.sub(" ", name).strip()
    return name


# ─── COMPANIES (Supabase) ─────────────────────────────────────────────────────

def load_companies_from_supabase(sb: Client) -> list:
    """
    Load all companies from Supabase. Returns list of company dicts with
    the same shape used throughout the rest of the script.
    Paginates 1000 rows at a time (Supabase default max).
    """
    companies = []
    offset    = 0
    page_size = 1000

    while True:
        try:
            result = (
                sb.table("companies")
                .select("slug, ats")
                .range(offset, offset + page_size - 1)
                .execute()
            )
        except Exception as e:
            logger.error(f"Failed to load companies from Supabase: {e}")
            return []

        rows = result.data or []
        for row in rows:
            slug = (row.get("slug") or "").strip()
            ats  = (row.get("ats")  or "").strip().lower()
            if not slug or not ats:
                continue
            companies.append({
                "slug": slug,
                "ats":  ats,
                "name": slug.replace("-", " ").replace("_", " ").title(),
            })

        if len(rows) < page_size:
            break
        offset += page_size

    logger.info(f"Loaded {len(companies)} companies from Supabase")
    return companies


def load_companies() -> list:
    sb = _get_supabase()
    if not sb:
        logger.error("SUPABASE_URL and SUPABASE_KEY must be set — cannot load companies")
        return []
    return load_companies_from_supabase(sb)


def load_watchlist(sb: Client) -> Dict[Tuple[str, str], dict]:
    """
    Load company_watchlist from Supabase.
    Returns dict keyed by (slug, ats) → {'priority': 'watch'|'high', 'notes': str}.
    """
    try:
        result = sb.table("company_watchlist").select("slug, ats, priority, notes").execute()
        return {
            (row["slug"], row["ats"]): {
                "priority": (row.get("priority") or "watch").lower(),
                "notes":    row.get("notes") or "",
            }
            for row in (result.data or [])
        }
    except Exception as e:
        logger.warning(f"Failed to load watchlist from Supabase: {e}")
        return {}


# Minimum fuzzy score to consider a contacts match valid.
_CONTACT_MATCH_THRESHOLD = 85

def load_contacts_index(sb: Client) -> Tuple[List[str], List[dict]]:
    """
    Load all contacts from Supabase for job-first fuzzy matching.
    Returns (norm_names, contact_rows) — norm_names[i] corresponds to contact_rows[i].
    At scoring time, match job.company_name against norm_names to find a connection.
    """
    try:
        result = sb.table("contacts").select(
            "id, slug, ats, linkedin_company, contact_name, contact_role, linkedin_url"
        ).execute()
        rows = result.data or []
        norm_names = [normalize_company(row.get("linkedin_company") or "") for row in rows]
        return norm_names, rows
    except Exception as e:
        logger.warning(f"Failed to load contacts index from Supabase: {e}")
        return [], []


def find_contacts(
    company_name: str,
    contact_norm_names: List[str],
    contact_rows: List[dict],
    slug: str = "",
    ats: str = "",
) -> List[dict]:
    """
    Return ALL contacts that match this job's company, via two strategies:

    1. Fuzzy name match: normalize job.company_name → match against contacts.linkedin_company.
       Catches the common case where the ATS returns a canonical company name (Greenhouse,
       Lever, Ashby all do this well). BambooHR falls back to slug_to_name() which is often
       good enough after normalization strips legal suffixes.

    2. Slug/ATS match: find contacts where contacts.slug == job.slug AND contacts.ats == job.ats.
       Catches contacts that were imported via the old slug-matching flow (linkedin_write.py),
       covering abbreviation slugs like 'ghx' or 'iqvia' that fuzzy name matching would miss.

    Results are deduplicated by contact id. Returns [] if no contacts found.
    """
    matched: Dict[str, dict] = {}  # keyed by contact id to deduplicate

    # Strategy 1: fuzzy company name match
    if company_name and contact_norm_names:
        norm = normalize_company(company_name)
        if norm:
            results = rfuzz_process.extract(
                norm, contact_norm_names, scorer=fuzz.token_sort_ratio, score_cutoff=_CONTACT_MATCH_THRESHOLD
            )
            for _, score, idx in results:
                row = contact_rows[idx]
                matched[row["id"]] = row

    # Strategy 2: slug/ats exact match (covers abbreviation slugs and migrated contacts)
    if slug and ats:
        for row in contact_rows:
            if row.get("slug") == slug and row.get("ats") == ats:
                matched[row["id"]] = row

    return list(matched.values())


# ─── GREENHOUSE ───────────────────────────────────────────────────────────────

_DEAD = "DEAD"   # sentinel returned by poll functions for permanent failures


def poll_greenhouse(company: dict):
    """
    Fetch all open jobs for a Greenhouse company.
    Returns stubs without descriptions — descriptions fetched on demand for title matches.
    Returns _DEAD sentinel on permanent failures (404).
    """
    slug = company["slug"]
    try:
        resp = requests.get(
            f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
            timeout=10,
        )
        if resp.status_code == 404:
            return _DEAD
        resp.raise_for_status()
        stubs = []
        for job in resp.json().get("jobs", []):
            stubs.append({
                "id":           f"gh_{job['id']}",
                "_job_id":      str(job["id"]),
                "title":        job.get("title", ""),
                "company_slug": slug,
                "company_name": company.get("name") or slug_to_name(slug),
                "ats":          "greenhouse",
                "url":          job.get("absolute_url", f"https://boards.greenhouse.io/{slug}/jobs/{job['id']}"),
                "location":     (job.get("location") or {}).get("name", ""),
                "posted_at":    job.get("updated_at", ""),
                "description":  "",
            })
        return stubs
    except Exception as e:
        logger.error(f"Greenhouse poll error ({slug}): {e}")
        return []


def fetch_greenhouse_job(slug: str, job_id: str, company_name: str) -> Optional[dict]:
    """Fetch a single Greenhouse job with full description."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}?content=true"
    try:
        resp = requests.get(url, timeout=10)
        if resp.status_code == 404:
            logger.debug(f"Greenhouse job 404: {slug}/{job_id}")
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
            "posted_at":    job.get("updated_at", ""),
            "description":  strip_html(job.get("content", "")),
        }
    except Exception as e:
        logger.error(f"Greenhouse detail fetch error ({slug}/{job_id}): {e}")
        return None


# ─── LEVER ────────────────────────────────────────────────────────────────────

def poll_lever(company: dict):
    """Fetch all open jobs for a Lever company, including full descriptions."""
    slug = company["slug"]
    name = company.get("name") or slug_to_name(slug)
    try:
        resp = requests.get(
            f"https://api.lever.co/v0/postings/{slug}?mode=json",
            timeout=10,
        )
        if resp.status_code == 404:
            return _DEAD
        resp.raise_for_status()
        jobs = []
        for job in resp.json():
            if not isinstance(job, dict):
                continue
            job_id    = job.get("id", "")
            posted_at = ""
            if job.get("createdAt"):
                posted_at = datetime.fromtimestamp(
                    job["createdAt"] / 1000, tz=timezone.utc
                ).isoformat()
            cats     = job.get("categories") or {}
            location = cats.get("location", "") or (
                cats.get("allLocations", [""])[0]
                if isinstance(cats.get("allLocations"), list) else ""
            )
            parts = [job.get("descriptionPlain", "")]
            for lst in job.get("lists", []):
                items = " ".join(lst.get("content", []))
                parts.append(f"{lst.get('text', '')}: {items}")
            jobs.append({
                "id":           f"lv_{job_id}",
                "title":        job.get("text", ""),
                "company_slug": slug,
                "company_name": name,
                "ats":          "lever",
                "url":          job.get("hostedUrl", f"https://jobs.lever.co/{slug}/{job_id}"),
                "location":     location,
                "posted_at":    posted_at,
                "description":  "\n".join(p for p in parts if p),
            })
        return jobs
    except Exception as e:
        logger.error(f"Lever poll error ({slug}): {e}")
        return []


# ─── ASHBY ────────────────────────────────────────────────────────────────────

def _parse_ashby_app_data(html: str) -> Optional[dict]:
    """Extract window.__appData JSON from an Ashby job board page."""
    scripts = re.findall(r'<script[^>]*>(.*?)</script>', html, re.DOTALL)
    decoder = json.JSONDecoder()
    for script in scripts:
        if '__appData' not in script:
            continue
        m = re.search(r'window\.__appData\s*=\s*(\{)', script)
        if not m:
            continue
        try:
            obj, _ = decoder.raw_decode(script, m.start(1))
            return obj
        except json.JSONDecodeError:
            continue
    return None


def poll_ashby(company: dict):
    """
    Fetch job stubs from Ashby by scraping window.__appData on the public job board page.
    The posting-api endpoint now requires auth; page HTML remains public.
    Returns stubs without descriptions — descriptions fetched on demand for title matches.
    """
    slug = company["slug"]
    name = company.get("name") or slug_to_name(slug)
    try:
        resp = requests.get(
            f"https://jobs.ashbyhq.com/{slug}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10,
        )
        if resp.status_code == 404:
            return _DEAD
        if resp.status_code != 200:
            return []
        data = _parse_ashby_app_data(resp.text)
        if not data:
            return []
        org_name = (data.get("organization") or {}).get("name") or name
        raw_jobs = (data.get("jobBoard") or {}).get("jobPostings") or []
        stubs = []
        for job in raw_jobs:
            job_id  = job.get("id", "")
            comp    = job.get("compensationTierSummary") or ""
            stubs.append({
                "id":            f"ash_{job_id}",
                "_job_id":       job_id,
                "_comp_summary": comp,
                "title":         job.get("title", ""),
                "company_slug":  slug,
                "company_name":  org_name,
                "ats":           "ashby",
                "url":           f"https://jobs.ashbyhq.com/{slug}/{job_id}",
                "location":      job.get("locationName") or "",
                "posted_at":     job.get("publishedDate", ""),
                "description":   "",
            })
        return stubs
    except Exception as e:
        logger.error(f"Ashby poll error ({slug}): {e}")
        return []


def fetch_ashby_job(stub: dict) -> Optional[dict]:
    """Fetch full description for an Ashby job from its individual job page HTML."""
    slug   = stub["company_slug"]
    job_id = stub.get("_job_id", "")
    if not job_id:
        return dict(stub)
    try:
        resp = requests.get(
            f"https://jobs.ashbyhq.com/{slug}/{job_id}",
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10,
        )
        if resp.status_code != 200:
            return dict(stub)
        data = _parse_ashby_app_data(resp.text)
        if not data:
            return dict(stub)
        posting = data.get("posting") or {}
        desc = strip_html(posting.get("descriptionHtml") or "")
        # Prepend compensation summary from listing data so Claude can see it
        comp = stub.get("_comp_summary") or ""
        if comp:
            desc = f"Compensation: {comp}\n\n{desc}"
        job = dict(stub)
        job["description"] = desc
        return job
    except Exception as e:
        logger.error(f"Ashby detail fetch error ({slug}/{job_id}): {e}")
        return dict(stub)


# ─── WORKABLE ─────────────────────────────────────────────────────────────────

def poll_workable(company: dict):
    """Fetch all open jobs for a Workable company."""
    slug = company["slug"]
    name = company.get("name") or slug_to_name(slug)
    try:
        resp = requests.post(
            f"https://apply.workable.com/api/v3/accounts/{slug}/jobs",
            json={},
            timeout=15,
        )
        if resp.status_code == 404:
            return _DEAD
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
                "company_name": name,
                "ats":          "workable",
                "url":          job.get("url") or f"https://apply.workable.com/{slug}/j/{shortcode}",
                "location":     ", ".join(p for p in loc_parts if p),
                "posted_at":    job.get("created_date") or job.get("published_on", ""),
                "description":  strip_html(job.get("description", "")),
            })
        return jobs
    except Exception as e:
        logger.error(f"Workable poll error ({slug}): {e}")
        return []


# ─── BAMBOOHR ─────────────────────────────────────────────────────────────────

def poll_bamboohr(company: dict):
    """
    Fetch job stubs from BambooHR careers list.
    Descriptions are not in the list response — fetched separately for title matches.
    Returns _DEAD sentinel on permanent failures.
    """
    slug = company["slug"]
    name = company.get("name") or slug_to_name(slug)
    try:
        resp = requests.get(
            f"https://{slug}.bamboohr.com/careers/list",
            headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        if resp.status_code in (404, 401, 403):
            return _DEAD
        if resp.status_code >= 500:
            return []
        resp.raise_for_status()
        if "application/json" not in resp.headers.get("Content-Type", ""):
            return _DEAD
        stubs = []
        for job in resp.json().get("result", []):
            loc = job.get("location") or {}
            if isinstance(loc, dict):
                location = ", ".join(filter(None, [loc.get("city", ""), loc.get("state", "")]))
            else:
                location = str(loc) if loc else ""
            job_id = str(job.get("id", ""))
            stubs.append({
                "id":           f"bhr_{job_id}",
                "_job_id":      job_id,
                "title":        job.get("jobOpeningName", ""),
                "company_slug": slug,
                "company_name": name,
                "ats":          "bamboohr",
                "url":          f"https://{slug}.bamboohr.com/careers/{job_id}",
                "location":     location,
                "posted_at":    "",
                "description":  "",
            })
        return stubs
    except Exception as e:
        logger.error(f"BambooHR poll error ({slug}): {e}")
        return []


def fetch_bamboohr_job(stub: dict) -> Optional[dict]:
    """Scrape a BambooHR job detail page and extract the description."""
    slug   = stub["company_slug"]
    job_id = stub["_job_id"]
    url    = stub["url"]
    try:
        resp = requests.get(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"},
            timeout=10,
        )
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "lxml")
        desc = ""
        for selector in ["div.BambooRich", "[data-testid='Prose']", "div.description", "div#app main", "main"]:
            el = soup.select_one(selector)
            if el:
                desc = el.get_text(separator=" ", strip=True)[:4000]
                break
        job = dict(stub)
        job["description"] = desc
        return job
    except Exception as e:
        logger.error(f"BambooHR detail fetch error ({slug}/{job_id}): {e}")
        return None


# ─── BREEZY ───────────────────────────────────────────────────────────────────

def poll_breezy(company: dict):
    """Fetch all open jobs for a Breezy HR company."""
    slug = company["slug"]
    name = company.get("name") or slug_to_name(slug)
    try:
        resp = requests.get(f"https://{slug}.breezy.hr/json", timeout=10)
        if resp.status_code == 404:
            return _DEAD
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
                "company_name": name,
                "ats":          "breezy",
                "url":          job.get("url") or f"https://{slug}.breezy.hr/p/{job.get('_id', '')}",
                "location":     ", ".join(p for p in loc_parts if p),
                "posted_at":    job.get("published_date", ""),
                "description":  strip_html(job.get("description", "")),
            })
        return jobs
    except Exception as e:
        logger.error(f"Breezy poll error ({slug}): {e}")
        return []


# ─── FILTERING ────────────────────────────────────────────────────────────────

def is_relevant_title(title: str) -> bool:
    t = title.lower()
    if any(rt.lower() in t for rt in ROLE_TITLES):
        return True
    has_ops       = any(kw in t for kw in _OPS_TERMS)
    has_qualifier = any(q in t for q in _DOMAIN_QUALS)
    return has_ops and has_qualifier


def is_within_lookback(posted_at: str, hours: int) -> bool:
    if not posted_at:
        return True  # no date = include (err on side of inclusion)
    dt = parse_iso_date(posted_at)
    if dt is None:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


def passes_hard_filters(job: dict) -> bool:
    title_lower = job.get("title", "").lower()

    if job.get("company_slug", "") in HARD_FILTERS["exclude_company_slugs"]:
        return False

    for kw in HARD_FILTERS["exclude_keywords"]:
        if kw.lower() in title_lower:
            return False

    if HARD_FILTERS["require_remote_or_austin"]:
        loc        = (job.get("location") or "").lower()
        desc_start = (job.get("description") or "")[:500].lower()

        non_us_countries = (
            "canada", "united kingdom", "uk ", " uk,", "europe", "germany",
            "france", "australia", "india", "mexico", "netherlands", "ireland",
            "spain", "brazil", "singapore", "poland", "sweden",
            "latin america", "latam", "argentina", "colombia", "chile",
            "new zealand", "south africa", "philippines", "nigeria",
        )
        if any(c in loc for c in non_us_countries):
            return False
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

def load_seen(sb: Optional[Client]) -> Set[str]:
    """
    Load seen job IDs from the Supabase jobs table (preferred) or local JSON fallback.
    The jobs table is the source of truth — seen_jobs table has been removed.
    Returns a set of job ID strings seen within the last 30 days.
    """
    if sb:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            result = sb.table("jobs").select("job_id").gte("last_seen_at", cutoff).execute()
            return {row["job_id"] for row in (result.data or [])}
        except Exception as e:
            logger.warning(f"Supabase jobs dedup load failed, falling back to local: {e}")

    # Local JSON fallback (used when Supabase is unreachable)
    if not os.path.exists(SEEN_JOBS_FILE):
        return set()
    try:
        with open(SEEN_JOBS_FILE) as f:
            data = json.load(f)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        return {k for k, v in data.items() if v >= cutoff}
    except Exception:
        return set()


def save_seen(sb: Optional[Client], new_ids: List[str]) -> None:
    """
    Update the local JSON fallback with newly seen job IDs.
    Supabase persistence is handled by save_scored_job() — no separate seen_jobs write needed.
    """
    if not new_ids:
        return
    # Local JSON fallback only — keeps dedup working if Supabase was unreachable this run
    try:
        existing = {}
        if os.path.exists(SEEN_JOBS_FILE):
            with open(SEEN_JOBS_FILE) as f:
                existing = json.load(f)
        now = datetime.now(timezone.utc).isoformat()
        for jid in new_ids:
            existing[jid] = now
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        pruned = {k: v for k, v in existing.items() if v >= cutoff}
        with open(SEEN_JOBS_FILE, "w") as f:
            json.dump(pruned, f)
    except Exception as e:
        logger.error(f"Failed to update local seen cache: {e}")


# ─── DEAD SLUG CACHE ──────────────────────────────────────────────────────────
# Tracks companies that consistently fail (404, 401, timeout) so we skip them
# for 30 days instead of wasting time on every run.

def load_dead_slugs(sb: Optional[Client]) -> Set[str]:
    """
    Load dead slugs from Supabase (preferred) or local JSON fallback.
    Returns a set of "ats/slug" strings.
    """
    if sb:
        try:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
            result = (
                sb.table("dead_slugs")
                .select("ats, slug")
                .gte("cached_at", cutoff)
                .execute()
            )
            return {f"{row['ats']}/{row['slug']}" for row in (result.data or [])}
        except Exception as e:
            logger.warning(f"Supabase dead_slugs load failed, falling back to local: {e}")

    # Local JSON fallback
    if not os.path.exists(DEAD_SLUGS_FILE):
        return set()
    try:
        with open(DEAD_SLUGS_FILE) as f:
            data = json.load(f)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        return {k for k, v in data.items() if v >= cutoff}
    except Exception:
        return set()


def save_dead_slugs(sb: Optional[Client], new_dead: Set[str]) -> None:
    """Persist newly discovered dead slugs. Only saves new ones."""
    if not new_dead:
        return
    now = datetime.now(timezone.utc).isoformat()

    if sb:
        try:
            rows = []
            for key in new_dead:
                parts = key.split("/", 1)
                if len(parts) == 2:
                    rows.append({"ats": parts[0], "slug": parts[1], "cached_at": now})
            for i in range(0, len(rows), 500):
                sb.table("dead_slugs").upsert(rows[i:i+500]).execute()
            return
        except Exception as e:
            logger.warning(f"Supabase dead_slugs save failed, falling back to local: {e}")

    # Local JSON fallback
    try:
        existing = {}
        if os.path.exists(DEAD_SLUGS_FILE):
            with open(DEAD_SLUGS_FILE) as f:
                existing = json.load(f)
        for key in new_dead:
            existing[key] = now
        cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        pruned = {k: v for k, v in existing.items() if v >= cutoff}
        with open(DEAD_SLUGS_FILE, "w") as f:
            json.dump(pruned, f)
    except Exception as e:
        logger.error(f"Failed to save dead slugs cache: {e}")


# ─── SCORING ──────────────────────────────────────────────────────────────────

# Anthropic client — created once per process, reused across all scoring calls
_anthropic_client: Optional[Anthropic] = None

# System prompt with cache_control — built once at module load from constants.
# The Anthropic API caches this prefix (5-min TTL, resets on each cache hit),
# so every job after the first pays ~10% of normal input cost for these tokens.
_SCORING_SYSTEM: list = [
    {
        "type": "text",
        "text": (
            "You are evaluating job postings for a specific candidate. "
            "Score the fit and provide actionable insights.\n\n"
            "CANDIDATE PROFILE:\n" + CANDIDATE_PROFILE + "\n\n"
            "MANDATORY SCORING RULES — apply these before general fit assessment:\n"
            + SCORING_RULES
            + "\n\n"
            'Respond ONLY with a JSON object (no markdown, no explanation):\n'
            '{\n'
            '  "score": <integer 0-100, after applying all mandatory rules above>,\n'
            '  "tier": <"A" if score>=80, "B" if 60-79, "C" if 40-59, "skip" if <40>,\n'
            '  "title_match": <true/false>,\n'
            '  "location_ok": <true/false>,\n'
            '  "top_matches": [<3 specific reasons this is a strong match>],\n'
            '  "gaps": [<up to 3 specific gaps or concerns, referencing mandatory rules where triggered>],\n'
            '  "apply_urgency": <"high"|"medium"|"low">,\n'
            '  "one_liner": <one sentence summary of fit, max 20 words>,\n'
            '  "outreach_angle": <if has_contact true: one sentence on best angle for reaching out, else null>\n'
            '}\n\n'
            "Scoring guide (before mandatory rule adjustments):\n"
            "- 80-100: Strong match on scope, industry, and skills\n"
            "- 60-79: Good match with minor gaps, worth applying\n"
            "- 40-59: Partial match, missing key elements\n"
            "- Below 40: Poor fit, skip"
        ),
        "cache_control": {"type": "ephemeral"},
    }
]


def score_job(job: dict, contacts: List[dict]) -> dict:
    """
    Score a job against the candidate profile.
    contacts: all matching rows from the contacts table (may be empty).
    All contact names/roles are included in the user message so Claude can
    personalize outreach_angle and reference specific connections.
    """
    global _anthropic_client
    if _anthropic_client is None:
        _anthropic_client = Anthropic(api_key=ANTHROPIC_API_KEY)

    has_contact = len(contacts) > 0
    if has_contact:
        names = ", ".join(
            f"{c.get('contact_name') or 'Unknown'} ({c.get('contact_role') or 'unknown role'})"
            for c in contacts
        )
        contact_line = f"has_contact: true  # connections: {names}\n"
    else:
        contact_line = "has_contact: false\n"

    user_content = (
        f"Title: {job['title']}\n"
        f"Company: {job['company_name']}\n"
        f"Location: {job.get('location', '')}\n"
        f"ATS: {job['ats']}\n"
        f"URL: {job['url']}\n"
        f"{contact_line}\n"
        f"Description:\n{job.get('description') or ''}"
    )

    try:
        response = _anthropic_client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=1000,
            system=_SCORING_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
        usage = response.usage
        cached  = getattr(usage, "cache_read_input_tokens", 0) or 0
        written = getattr(usage, "cache_creation_input_tokens", 0) or 0
        if cached:
            logger.info(f"  Cache hit — {cached:,} tokens served from cache")
        elif written:
            logger.info(f"  Cache write — {written:,} tokens cached for this run")

        raw = response.content[0].text.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        scoring = json.loads(raw.strip())
        if has_contact:
            scoring["score"] = min(100, scoring["score"] + CONTACT_SCORE_BOOST)
        scoring["has_contact"]   = has_contact
        scoring["contact_count"] = len(contacts)
        return scoring
    except Exception as e:
        logger.error(f"Scoring error for '{job.get('title', '?')}': {e}")
        return {
            "score": 50, "tier": "B", "title_match": True, "location_ok": True,
            "top_matches": ["Could not auto-score — review manually"],
            "gaps": ["Scoring failed"], "apply_urgency": "medium",
            "one_liner": "Manual review needed",
            "outreach_angle": None, "has_contact": has_contact, "contact_count": len(contacts),
        }


# ─── PUSHOVER ─────────────────────────────────────────────────────────────────

def send_pushover(job: dict, scoring: dict) -> None:
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
                "token":     PUSHOVER_APP_TOKEN,
                "user":      PUSHOVER_USER_KEY,
                "title":     title,
                "message":   message,
                "priority":  1 if scoring.get("apply_urgency") == "high" else 0,
                "url":       job.get("url", ""),
                "url_title": "View Job",
            },
            timeout=10,
        )
        logger.info(f"Pushover sent for: {job['title']} @ {company}")
    except Exception as e:
        logger.error(f"Pushover error: {e}")


# ─── JOBS (Supabase analytics) ────────────────────────────────────────────────

def save_scored_job(sb: Optional[Client], job: dict, scoring: dict, pushed_to_notion: bool) -> None:
    """Persist a scored job to the Supabase jobs table for analytics."""
    if not sb:
        return
    try:
        row = {
            "job_id":           job.get("id", ""),
            "ats":              job.get("ats", ""),
            "company_slug":     job.get("company_slug", ""),
            "title":            job.get("title", ""),
            "location":         job.get("location", ""),
            "url":              job.get("url", ""),
            "score":            scoring.get("score"),
            "tier":             scoring.get("tier"),
            "gaps":             " | ".join(scoring.get("gaps") or []),
            "top_matches":      " | ".join(scoring.get("top_matches") or []),
            "posted_at":        (job.get("posted_at") or "")[:10] or None,
            "pushed_to_notion": pushed_to_notion,
            "last_seen_at":     datetime.now(timezone.utc).isoformat(),
        }
        sb.table("jobs").upsert(row, on_conflict="job_id").execute()
    except Exception as e:
        logger.warning(f"Failed to save job to Supabase jobs table: {e}")


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
    print(f"COMPANY: {company} | HAS CONTACT: {scoring.get('has_contact', False)}")
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

def _poll_one_company(company: dict, seen: Set[str], skippable_dead: Set[str]) -> Tuple[List[dict], Set[str]]:
    """
    Poll a single company's ATS. Returns (candidates, new_dead_keys).
    Called in parallel from worker threads — no shared mutable state written here.
    """
    ats      = (company.get("ats") or "").lower()
    slug     = company.get("slug", "")
    dead_key = f"{ats}/{slug}"
    candidates: List[dict] = []
    new_dead:   Set[str]   = set()

    if not slug or not ats:
        return candidates, new_dead
    if dead_key in skippable_dead:
        return candidates, new_dead

    try:
        if ats == "greenhouse":
            stubs = poll_greenhouse(company)
            if stubs is _DEAD:
                new_dead.add(dead_key)
            else:
                for stub in stubs:
                    if stub["id"] in seen or not is_relevant_title(stub["title"]):
                        continue
                    job = fetch_greenhouse_job(stub["company_slug"], stub["_job_id"], stub["company_name"])
                    if job:
                        candidates.append(job)

        elif ats == "lever":
            result = poll_lever(company)
            if result is _DEAD:
                new_dead.add(dead_key)
            else:
                candidates.extend(j for j in result if j["id"] not in seen and is_relevant_title(j["title"]))

        elif ats == "ashby":
            stubs = poll_ashby(company)
            if stubs is _DEAD:
                new_dead.add(dead_key)
            else:
                for stub in stubs:
                    if stub["id"] in seen or not is_relevant_title(stub["title"]):
                        continue
                    job = fetch_ashby_job(stub)
                    if job:
                        candidates.append(job)

        elif ats == "workable":
            result = poll_workable(company)
            if result is _DEAD:
                new_dead.add(dead_key)
            else:
                candidates.extend(j for j in result if j["id"] not in seen and is_relevant_title(j["title"]))

        elif ats == "bamboohr":
            stubs = poll_bamboohr(company)
            if stubs is _DEAD:
                new_dead.add(dead_key)
            else:
                for stub in stubs:
                    if stub["id"] in seen or not is_relevant_title(stub["title"]):
                        continue
                    job = fetch_bamboohr_job(stub)
                    if job:
                        candidates.append(job)

        elif ats == "breezy":
            result = poll_breezy(company)
            if result is _DEAD:
                new_dead.add(dead_key)
            else:
                candidates.extend(j for j in result if j["id"] not in seen and is_relevant_title(j["title"]))

        else:
            logger.debug(f"Unknown ATS '{ats}' for {slug} — skipping")

    except Exception as e:
        logger.error(f"Error polling {ats}/{slug}: {e}")

    return candidates, new_dead


def run_scout(dry_run: bool = False, hours_back: int = HOURS_LOOKBACK) -> list:
    logger.info(f"Job Scout starting — Supabase + parallel polling | dry_run={dry_run} | lookback={hours_back}h")

    sb        = _get_supabase()
    seen      = load_seen(sb)
    dead      = load_dead_slugs(sb)
    companies = load_companies()

    if not companies:
        logger.error("No companies loaded — check SUPABASE_URL and SUPABASE_KEY")
        return []

    # Load watchlist and contacts index — used at scoring time, not at poll time.
    watchlist               = load_watchlist(sb) if sb else {}
    contact_norm_names, contact_rows = load_contacts_index(sb) if sb else ([], [])

    watch_slugs    = {slug for (slug, ats), w in watchlist.items() if w["priority"] == "watch"}
    high_slugs     = {slug for (slug, ats), w in watchlist.items() if w["priority"] == "high"}
    always_surface = watch_slugs | high_slugs

    # Never skip watchlist companies regardless of dead cache.
    # Contact boost is determined at scoring time, not poll time.
    skippable_dead = {k for k in dead if k.split("/", 1)[-1] not in always_surface}

    logger.info(
        f"Companies: {len(companies)} total | "
        f"{len(contact_rows)} contacts in index | {len(watch_slugs)} watch | {len(high_slugs)} high | "
        f"{len(skippable_dead)} cached dead"
    )

    # ── Parallel ATS polling ───────────────────────────────────────────────────
    # Group companies by ATS platform, run each platform with its own worker pool
    by_ats: Dict[str, List[dict]] = {}
    for c in companies:
        ats = (c.get("ats") or "").lower()
        if ats:
            by_ats.setdefault(ats, []).append(c)

    all_candidates: List[dict] = []
    all_new_dead:   Set[str]   = set()
    total_polled = 0

    for ats, ats_companies in by_ats.items():
        workers    = PLATFORM_WORKERS.get(ats, 5)
        active     = [c for c in ats_companies if f"{ats}/{c['slug']}" not in skippable_dead]
        skip_count = len(ats_companies) - len(active)
        logger.info(f"  [{ats.upper()}] {len(active)} companies ({skip_count} skipped from dead cache) — {workers} workers")

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(_poll_one_company, company, seen, skippable_dead): company
                for company in active
            }
            pending_dead: Set[str] = set()
            for future in as_completed(futures):
                try:
                    cands, new_dead = future.result()
                    all_candidates.extend(cands)
                    all_new_dead.update(new_dead)
                    pending_dead.update(new_dead)
                except Exception as e:
                    co = futures[future]
                    logger.error(f"Worker error for {co.get('ats')}/{co.get('slug')}: {e}")
                total_polled += 1
                # Flush dead slugs to Supabase every 500 to preserve progress on cancel
                if len(pending_dead) >= 500:
                    save_dead_slugs(sb, pending_dead)
                    pending_dead.clear()

            # Flush any remaining dead slugs for this platform
            if pending_dead:
                save_dead_slugs(sb, pending_dead)
                pending_dead.clear()

        logger.info(f"  [{ats.upper()}] done — {len(all_candidates)} candidates so far")

    logger.info(f"Polling complete: {total_polled} companies polled | {len(all_candidates)} candidates | {len(all_new_dead)} new dead")

    # ── Filter ─────────────────────────────────────────────────────────────────
    relevant: list = []
    for job in all_candidates:
        if not passes_hard_filters(job):
            continue
        is_priority = job.get("company_slug", "") in always_surface
        if not is_priority and not is_within_lookback(job.get("posted_at", ""), hours_back):
            continue
        relevant.append(job)

    logger.info(f"Relevant new jobs after filtering: {len(relevant)}")

    # ── Separate out empty-description jobs — don't score, don't mark seen ─────
    no_desc:      list = []
    to_score:     list = []
    for job in relevant:
        if not (job.get("description") or "").strip():
            no_desc.append(job)
        else:
            to_score.append(job)

    if no_desc:
        logger.info(f"  {len(no_desc)} jobs have no description — skipping scoring, will retry next run")

    if not to_score:
        logger.info("No new relevant jobs with descriptions found.")
        # Still print no-description list before returning
        if no_desc:
            print(f"\n{'='*60}")
            print(f"NO DESCRIPTION — review manually ({len(no_desc)} jobs):")
            print('='*60)
            for j in sorted(no_desc, key=lambda x: x.get("title", "")):
                co = j.get("company_name", j.get("company_slug", ""))
                print(f"  {j.get('title','?')} @ {co}")
                print(f"       └─ {j.get('url','')}")
            print('='*60)
        return []

    relevant = to_score

    # ── Score and push (sequential — Claude API rate limits) ───────────────────
    results:  list      = []
    skipped:  list      = []
    new_seen: List[str] = []

    for job in relevant:
        company = job.get("company_name", job.get("company_slug", ""))
        logger.info(f"Scoring: {job.get('title', '?')} @ {company}...")
        slug    = job.get("company_slug", "")
        contacts = find_contacts(
            job.get("company_name", ""), contact_norm_names, contact_rows,
            slug=job.get("company_slug", ""), ats=job.get("ats", ""),
        )
        if contacts:
            names = ", ".join(c.get("contact_name") or "?" for c in contacts)
            logger.info(f"  Contact match ({len(contacts)}): {names}")
        scoring = score_job(job, contacts)

        # Apply watchlist priority boosts (on top of any contact boost from score_job)
        if slug in high_slugs:
            scoring["score"] = min(100, scoring["score"] + HIGH_SCORE_BOOST)
        elif slug in watch_slugs:
            scoring["score"] = min(100, scoring["score"] + WATCH_SCORE_BOOST)

        # Recalculate tier after boosts
        s = scoring["score"]
        scoring["tier"] = "A" if s >= 80 else "B" if s >= 60 else "C" if s >= 40 else "skip"

        is_priority = slug in always_surface
        if scoring["score"] < SCORE_THRESHOLD and not is_priority:
            logger.info(f"  Score {scoring['score']} below threshold — skipping")
            save_scored_job(sb, job, scoring, pushed_to_notion=False)
            new_seen.append(job["id"])
            if dry_run:
                skipped.append({"job": job, "scoring": scoring})
            time.sleep(1)
            continue

        logger.info(f"  Score: {scoring['score']} ({scoring['tier']}) — {scoring['one_liner']}")

        if dry_run:
            print_job(job, scoring)
            save_scored_job(sb, job, scoring, pushed_to_notion=False)
        else:
            pushed = push_to_notion(job, scoring)
            save_scored_job(sb, job, scoring, pushed_to_notion=pushed)
            if scoring.get("tier") == "A":
                send_pushover(job, scoring)

        new_seen.append(job["id"])
        results.append({"job": job, "scoring": scoring})
        time.sleep(1)

    # ── Dry-run: print skipped summary ─────────────────────────────────────────
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

    # ── Print no-description jobs for manual review ────────────────────────────
    if no_desc:
        print(f"\n{'='*60}")
        print(f"NO DESCRIPTION — review manually ({len(no_desc)} jobs):")
        print('='*60)
        for j in sorted(no_desc, key=lambda x: x.get("title", "")):
            co = j.get("company_name", j.get("company_slug", ""))
            print(f"  {j.get('title','?')} @ {co}")
            print(f"       └─ {j.get('url','')}")
        print('='*60)

    save_seen(sb, new_seen)
    logger.info(f"Done. {len(results)} jobs pushed above threshold | {len(no_desc)} need manual description review.")
    return results


# ─── CLI ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Job Scout — direct ATS polling and AI scoring")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print results to terminal, don't push to Notion")
    parser.add_argument("--hours", type=int, default=HOURS_LOOKBACK, metavar="N",
                        help=f"Only process jobs posted in the last N hours (default {HOURS_LOOKBACK}). "
                             "Use a large value (e.g. 168) on first run to backfill.")
    args = parser.parse_args()
    run_scout(dry_run=args.dry_run, hours_back=args.hours)


if __name__ == "__main__":
    main()
