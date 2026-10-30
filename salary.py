"""Deterministic salary parsing + hard gate.

parse_salary() extracts a comp range from free text.
passes_salary_gate() applies the hard drop: if max stated base comp is below
SALARY_FLOOR_BASE, drop the role before calling Claude (saves API cost).

Missing salary → always passes (never gate on absent data).
OTE/total-comp-only → always passes (can't determine base from OTE alone).
"""

import re
from typing import Dict, Optional

import config as _config

# Optional — set SALARY_FLOOR_BASE = 0 in config.py to turn the gate off.
SALARY_FLOOR_BASE = getattr(_config, "SALARY_FLOOR_BASE", 0)

# Matches dollar amounts in common job-description formats:
#   $200,000 | $200K | $200k | $200 (bare 3-digit = treated as $200k)
#   USD 200,000 | USD200K | US$200,000
_MONEY = re.compile(
    r"(?:\$|USD\s?|US\$)\s?(\d{1,3}(?:,\d{3})+|\d{2,3})\s?([kK])?\b",
    re.IGNORECASE,
)

# Markers meaning the nearby figure is total/variable comp, not base salary
_OTE_MARKERS = ("ote", "on-target", "on target", "total comp", "total target", "uncapped")

# Any figure below this is noise (401k contribution %, stipends, etc.), not comp
_MIN_PLAUSIBLE = 50_000

# Retirement plan references — must never be read as salary
_RETIREMENT = re.compile(r"\$?\b(?:401|403|457)\s?\(?[kb]\)?", re.IGNORECASE)

# Equity / bonus / grant / sign-on figures — not base salary
_NON_BASE = re.compile(
    r"\$?\b(?:\d{1,3}(?:,\d{3})+|\d{2,3})\s?[kK]?\b\s*(?:in\s+)?(?:equity|rsus?|stock|grant|signing\s+bonus|sign[- ]?on\s+bonus|bonus)"
    r"|(?:equity|rsus?|stock|grant|signing\s+bonus|sign[- ]?on\s+bonus|bonus)\s+(?:of\s+)?\$?\b(?:\d{1,3}(?:,\d{3})+|\d{2,3})\s?[kK]?\b",
    re.IGNORECASE,
)


def _to_dollars(num_str: str, has_k: bool) -> int:
    raw = int(num_str.replace(",", ""))
    if has_k:
        raw *= 1_000
    elif 100 <= raw < 1_000:
        # bare 3-digit number with no 'k' — "$200" → $200k, "$50" stays at 50 (noise)
        raw *= 1_000
    return raw


def parse_salary(text: str) -> Dict[str, Optional[int]]:
    """Return {"min": int|None, "max": int|None, "is_ote": bool}.

    min/max are None when no plausible base comp figure is found.
    min == max for a single stated value.
    """
    if not text:
        return {"min": None, "max": None, "is_ote": False}

    low = text.lower()
    is_ote = any(m in low for m in _OTE_MARKERS)

    # Strip retirement refs and non-base figures before extracting numbers
    cleaned = _RETIREMENT.sub("", text)
    cleaned = _NON_BASE.sub("", cleaned)

    values = []
    for m in _MONEY.finditer(cleaned):
        dollars = _to_dollars(m.group(1), bool(m.group(2)))
        if dollars >= _MIN_PLAUSIBLE:
            values.append(dollars)

    if not values:
        return {"min": None, "max": None, "is_ote": is_ote}

    return {"min": min(values), "max": max(values), "is_ote": is_ote}


def passes_salary_gate(parsed: Dict) -> bool:
    """True = keep the job; False = hard drop before scoring.

    Hard drops only when we have high confidence the role is below floor:
    - max stated base comp < SALARY_FLOOR_BASE
    Missing salary → keep. OTE-only → keep (can't determine base).
    """
    if not SALARY_FLOOR_BASE or parsed["max"] is None:
        return True  # gate disabled, or no salary data — never gate on absence
    if parsed["is_ote"]:
        return True  # OTE figure only — gate on base, not total comp
    return parsed["max"] >= SALARY_FLOOR_BASE
