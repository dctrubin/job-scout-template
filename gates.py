"""Deterministic function gate.

Reads the team_function label Claude assigns to a role and hard-drops roles
whose actual function is outside your target lanes. This is belt-and-suspenders
for mis-titled roles that slip through title filtering — e.g. "Policy & Strategy"
at a law firm that scores well on title but is clearly a legal function, not ops.
"""

import re
from typing import Optional

import config as _config

# Optional — an empty list (or no setting at all) disables the flag.
DISQUALIFY_FUNCTIONS = getattr(_config, "DISQUALIFY_FUNCTIONS", [])


def function_gate_reason(scoring: dict) -> Optional[str]:
    """Return a drop reason string if team_function is disqualifying, else None.

    Missing or blank team_function → None (let score threshold decide).
    """
    fn = (scoring.get("team_function") or "").strip().lower()
    if not fn:
        return None
    for bad in DISQUALIFY_FUNCTIONS:
        if re.search(rf"\b{re.escape(bad)}\b", fn):
            return f"function gate: '{scoring.get('team_function')}' outside target lanes"
    return None
