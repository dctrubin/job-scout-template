#!/usr/bin/env python3
"""
seed_companies.py — Bulk company seeder for Job Scout

Fetches company slugs from pre-built GitHub JSON files (job-board-aggregator)
for Greenhouse, Lever, and Ashby, then optionally bulk-loads them into the
Notion companies database.

Usage:
    # Step 1 — generate CSV for review
    python3 seed_companies.py --output companies_seed.csv

    # Step 2 — load reviewed CSV into Notion (skips slugs already present)
    python3 seed_companies.py --load --input companies_seed.csv

    # Do both in one shot (skips manual review)
    python3 seed_companies.py --output companies_seed.csv --load

    # Single platform only
    python3 seed_companies.py --output companies_seed.csv --platform greenhouse

    # Fall back to Common Crawl instead of GitHub (slower, less reliable)
    python3 seed_companies.py --output companies_seed.csv --source commoncrawl
"""

import os
import json
import csv
import re
import time
import argparse
import logging
from urllib.parse import urlparse

from typing import Optional, Set, Tuple, List, Dict

import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ─── SETTINGS ─────────────────────────────────────────────────────────────────

NOTION_API_KEY         = os.environ.get("NOTION_API_KEY", "")
NOTION_COMPANIES_DB_ID = os.environ.get("NOTION_COMPANIES_DB_ID", "")
SUPABASE_URL           = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY           = os.environ.get("SUPABASE_KEY", "")

NOTION_HEADERS = {
    "Authorization": f"Bearer {NOTION_API_KEY}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28",
}

# ─── GITHUB SOURCE (primary) ──────────────────────────────────────────────────
#
# Pre-built slug lists from job-board-aggregator (CC BY-NC 4.0 — personal use OK)
# https://github.com/Feashliaa/job-board-aggregator
# Workable is not in this repo — kept as Common Crawl only (or add manually)

GITHUB_SOURCES = {
    "greenhouse": "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/greenhouse_companies.json",
    "lever":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/lever_companies.json",
    "ashby":      "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/ashby_companies.json",
    "bamboohr":   "https://raw.githubusercontent.com/Feashliaa/job-board-aggregator/main/data/bamboohr_companies.json",
}

# ─── COMMON CRAWL SOURCE (fallback / Workable) ────────────────────────────────

CC_INDEXES = [
    "CC-MAIN-2026-08",  # Feb 2026 — confirmed working
    "CC-MAIN-2026-04",  # Jan 2026
    "CC-MAIN-2025-51",  # Dec 2025
    "CC-MAIN-2025-47",  # Nov 2025
    "CC-MAIN-2024-51",  # Dec 2024 — confirmed working fallback
]

CC_PLATFORMS = {
    "greenhouse": "boards.greenhouse.io",
    "lever":      "jobs.lever.co",
    "ashby":      "jobs.ashbyhq.com",
    "workable":   "apply.workable.com",
}

# All platforms available (union of both sources)
ALL_PLATFORMS = {"greenhouse", "lever", "ashby", "bamboohr", "workable"}

# First path segments that are not company slugs
_SKIP_SLUGS = {
    "", "api", "about", "careers", "jobs", "apply", "search",
    "404", "error", "login", "auth", "terms", "privacy", "sitemap",
    "static", "assets", "cdn", "embed", "widget", "feed",
}

# Notion API rate limit — max 3 requests/second
_NOTION_DELAY = 0.4

# Chunk size for Common Crawl pagination
_CC_PAGE_SIZE = 50_000


# ─── GITHUB FETCH (primary) ───────────────────────────────────────────────────

def fetch_slugs_from_github(platform: str) -> Set[str]:
    """
    Fetch pre-built company slug list from job-board-aggregator GitHub repo.
    Returns a set of slug strings, or empty set on failure.
    """
    url = GITHUB_SOURCES.get(platform)
    if not url:
        logger.warning(f"  [{platform}] No GitHub source available — skipping")
        return set()

    logger.info(f"  [{platform}] Fetching from GitHub...")
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data, list):
            logger.error(f"  [{platform}] Unexpected response format from GitHub (expected list)")
            return set()
        slugs = {s.strip().lower() for s in data if isinstance(s, str) and s.strip()}
        logger.info(f"  [{platform}] {len(slugs)} slugs from GitHub")
        return slugs
    except Exception as e:
        logger.error(f"  [{platform}] GitHub fetch failed: {e}")
        return set()


# ─── COMMON CRAWL FETCH (fallback) ────────────────────────────────────────────

def extract_slug(raw_url: str) -> Optional[str]:
    """Extract company slug from an ATS URL (first path segment)."""
    try:
        path = urlparse(raw_url).path.strip("/")
        if not path:
            return None
        slug = path.split("/")[0].lower()
        if not slug or slug in _SKIP_SLUGS or len(slug) < 2:
            return None
        if "%" in slug or slug.startswith("?") or slug.startswith("#"):
            return None
        if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", slug):
            return None
        return slug
    except Exception:
        return None


def fetch_slugs_from_commoncrawl(platform: str, domain: str) -> Set[str]:
    """
    Query Common Crawl for all URLs matching a domain pattern and return
    unique company slugs. Tries each index in CC_INDEXES until one works.
    """
    slugs: Set[str] = set()

    for index in CC_INDEXES:
        base_url = f"https://index.commoncrawl.org/{index}-index"
        offset = 0
        index_worked = False

        logger.info(f"  [{platform}] Querying {index}...")

        while True:
            params = {
                "url":    f"{domain}/*",
                "output": "json",
                "fl":     "url",
                "limit":  _CC_PAGE_SIZE,
                "offset": offset,
            }
            success = False
            for attempt in range(3):
                try:
                    resp = requests.get(base_url, params=params, timeout=90, stream=True)
                    if resp.status_code in (503, 504):
                        wait = 10 * (attempt + 1)
                        logger.warning(f"  [{platform}] {index} returned {resp.status_code} (attempt {attempt+1}/3) — waiting {wait}s")
                        time.sleep(wait)
                        continue
                    resp.raise_for_status()
                    success = True
                    break
                except requests.RequestException as e:
                    wait = 10 * (attempt + 1)
                    logger.warning(f"  [{platform}] {index} request failed (attempt {attempt+1}/3): {e} — waiting {wait}s")
                    time.sleep(wait)

            if not success:
                logger.warning(f"  [{platform}] {index} failed after 3 attempts — trying next index")
                break

            page_count = 0
            for raw_line in resp.iter_lines():
                if not raw_line:
                    continue
                line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
                job_url = ""
                try:
                    data = json.loads(line)
                    job_url = data.get("url", "")
                except (json.JSONDecodeError, AttributeError):
                    job_url = line.strip()

                slug = extract_slug(job_url)
                if slug:
                    slugs.add(slug)
                page_count += 1

            index_worked = True
            logger.info(f"  [{platform}] {index} offset={offset}: {page_count} lines, {len(slugs)} slugs so far")

            if page_count < _CC_PAGE_SIZE:
                break

            offset += _CC_PAGE_SIZE
            time.sleep(0.5)

        if index_worked:
            logger.info(f"  [{platform}] Done — {len(slugs)} unique slugs from {domain}")
            return slugs

        time.sleep(2)

    logger.warning(f"  [{platform}] All indexes failed for {domain} — 0 slugs returned")
    return slugs


# ─── NOTION HELPERS ───────────────────────────────────────────────────────────

def load_existing_notion_slugs() -> Set[Tuple[str, str]]:
    """Return (slug, ats) pairs already in the Notion companies DB for dedup."""
    existing: Set[Tuple[str, str]] = set()
    cursor = None

    logger.info("Loading existing Notion companies (for dedup)...")

    while True:
        body: dict = {"page_size": 100}
        if cursor:
            body["start_cursor"] = cursor

        try:
            resp = requests.post(
                f"https://api.notion.com/v1/databases/{NOTION_COMPANIES_DB_ID}/query",
                headers=NOTION_HEADERS,
                json=body,
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logger.error(f"Failed to load existing Notion companies: {e}")
            return existing

        for page in data.get("results", []):
            props = page.get("properties", {})

            def txt(key: str) -> str:
                p = props.get(key, {})
                items = p.get("rich_text") or p.get("title") or []
                return items[0]["plain_text"].strip().lower() if items else ""

            def sel(key: str) -> str:
                s = props.get(key, {}).get("select") or {}
                return s.get("name", "").lower()

            slug = txt("Slug")
            ats  = sel("ATS")
            if slug and ats:
                existing.add((slug, ats))

        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
        time.sleep(_NOTION_DELAY)

    logger.info(f"Found {len(existing)} existing companies in Notion")
    return existing


def create_notion_company(slug: str, ats: str) -> bool:
    """Create one company page in the Notion companies DB."""
    display_name = slug.replace("-", " ").replace("_", " ").title()

    payload = {
        "parent": {"database_id": NOTION_COMPANIES_DB_ID},
        "properties": {
            "Name": {
                "title": [{"text": {"content": display_name}}]
            },
            "Slug": {
                "rich_text": [{"text": {"content": slug}}]
            },
            "ATS": {
                "select": {"name": ats}
            },
            "Priority": {
                "select": {"name": "Normal"}
            },
        },
    }

    try:
        resp = requests.post(
            "https://api.notion.com/v1/pages",
            headers=NOTION_HEADERS,
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as e:
        body = e.response.text if hasattr(e, "response") and e.response is not None else ""
        logger.error(f"Failed to create Notion page for {ats}/{slug}: {e} — {body[:200]}")
        return False


# ─── SUPABASE LOADER ──────────────────────────────────────────────────────────

def load_to_supabase(rows: List[Dict]) -> None:
    """
    Bulk upsert all rows into the Supabase companies table.
    Uses batches of 500 to stay within REST API limits.
    Skips rows that already exist (ON CONFLICT DO NOTHING via ignoreDuplicates).
    """
    if not SUPABASE_URL or not SUPABASE_KEY:
        logger.error("SUPABASE_URL and SUPABASE_KEY must be set to load to Supabase")
        return

    headers = {
        "apikey":        SUPABASE_KEY,
        "Authorization": f"Bearer {SUPABASE_KEY}",
        "Content-Type":  "application/json",
        "Prefer":        "resolution=ignore-duplicates",
    }
    url     = f"{SUPABASE_URL}/rest/v1/companies"
    batch   = 500
    success = 0
    fail    = 0

    logger.info(f"\n=== Loading {len(rows)} companies to Supabase (batches of {batch}) ===")

    for i in range(0, len(rows), batch):
        chunk = rows[i:i + batch]
        payload = [{"slug": r["slug"], "ats": r["ats"]} for r in chunk]
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=30)
            resp.raise_for_status()
            success += len(chunk)
        except Exception as e:
            body = e.response.text if hasattr(e, "response") and e.response is not None else ""
            logger.error(f"Supabase batch {i//batch + 1} failed: {e} — {body[:200]}")
            fail += len(chunk)

        if (i // batch + 1) % 20 == 0:
            logger.info(f"  Progress: {i + len(chunk)}/{len(rows)} ({success} ok, {fail} failed)")

    logger.info(f"\n=== Done: {success} inserted/skipped, {fail} failed ===")


# ─── CSV I/O ──────────────────────────────────────────────────────────────────

def write_csv(rows: List[Dict], path: str) -> None:
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["slug", "ats"])
        writer.writeheader()
        writer.writerows(rows)
    logger.info(f"Wrote {len(rows)} companies to {path}")


def read_csv(path: str) -> List[Dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


# ─── MAIN ─────────────────────────────────────────────────────────────────────

def run_seed(
    output_path: Optional[str],
    target: str,
    input_path: Optional[str],
    source: str,
    platforms: Set[str],
) -> None:

    rows: List[Dict] = []

    if input_path:
        logger.info(f"Loading from existing CSV: {input_path}")
        rows = read_csv(input_path)

    else:
        if source == "github":
            logger.info("=== Fetching company slugs from GitHub (job-board-aggregator) ===")
            for platform in sorted(platforms):
                if platform == "workable":
                    logger.info(f"  [workable] No GitHub source available — use --source commoncrawl for Workable")
                    continue
                slugs = fetch_slugs_from_github(platform)
                for slug in sorted(slugs):
                    rows.append({"slug": slug, "ats": platform})

        else:  # commoncrawl
            logger.info("=== Querying Common Crawl for company slugs ===")
            for platform in sorted(platforms):
                domain = CC_PLATFORMS.get(platform)
                if not domain:
                    continue
                logger.info(f"\n[{platform.upper()}] {domain}")
                slugs = fetch_slugs_from_commoncrawl(platform, domain)
                for slug in sorted(slugs):
                    rows.append({"slug": slug, "ats": platform})
                logger.info(f"[{platform.upper()}] {len(slugs)} unique slugs")

        logger.info(f"\n=== Total: {len(rows)} companies across all platforms ===")

    if output_path:
        write_csv(rows, output_path)

    if target == "none":
        return

    if target == "supabase":
        load_to_supabase(rows)
        return

    # target == "notion"
    if not NOTION_API_KEY or not NOTION_COMPANIES_DB_ID:
        logger.error("NOTION_API_KEY and NOTION_COMPANIES_DB_ID must be set to load to Notion")
        return

    existing = load_existing_notion_slugs()
    to_add   = [r for r in rows if (r["slug"], r["ats"]) not in existing]

    logger.info(f"\n=== Loading to Notion: {len(to_add)} new companies ({len(rows) - len(to_add)} already exist) ===")

    success = 0
    fail    = 0
    for i, row in enumerate(to_add, 1):
        ok = create_notion_company(row["slug"], row["ats"])
        if ok:
            success += 1
        else:
            fail += 1
        if i % 50 == 0:
            logger.info(f"  Progress: {i}/{len(to_add)} ({success} ok, {fail} failed)")
        time.sleep(_NOTION_DELAY)

    logger.info(f"\n=== Done: {success} created, {fail} failed ===")


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed Job Scout companies DB")
    parser.add_argument("--output", metavar="FILE",
                        help="Write slugs to this CSV file (e.g. companies_seed.csv)")
    parser.add_argument("--target", metavar="TARGET", default="supabase",
                        choices=["supabase", "notion", "none"],
                        help="Where to load companies: supabase (default), notion, or none (CSV only)")
    parser.add_argument("--input", metavar="FILE",
                        help="Load from an existing CSV instead of fetching")
    parser.add_argument("--platform", metavar="NAME",
                        help=f"Only seed one platform ({', '.join(sorted(ALL_PLATFORMS))})")
    parser.add_argument("--source", metavar="SOURCE", default="github",
                        choices=["github", "commoncrawl"],
                        help="Slug source: github (default, fast) or commoncrawl (slow, includes Workable)")
    args = parser.parse_args()

    if not args.output and not args.input and args.target == "none":
        parser.error("Specify --output, --input, or a --target to load into")

    platforms = ALL_PLATFORMS.copy()
    if args.platform:
        if args.platform not in ALL_PLATFORMS:
            parser.error(f"Unknown platform '{args.platform}'. Choose from: {', '.join(sorted(ALL_PLATFORMS))}")
        platforms = {args.platform}

    run_seed(
        output_path=args.output,
        target=args.target,
        input_path=args.input,
        source=args.source,
        platforms=platforms,
    )


if __name__ == "__main__":
    main()
