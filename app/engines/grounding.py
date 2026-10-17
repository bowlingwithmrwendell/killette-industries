"""Grounding check: every number in an AI draft must already appear in the
facts the engines returned. Anything else is flagged as unverified, and an
unverified draft cannot be approved unchanged.

Identifiers like 'HUB-02' or 'Q3' count as facts only if they appear in the
facts too, so the model cannot invent a hub, a milestone or a dollar figure.
"""
from __future__ import annotations

import json
import re
from decimal import Decimal

_NUM = re.compile(r"(?<![A-Za-z])-?\d+(?:[.,]\d+)*")


def numbers(text: str) -> set[str]:
    out = set()
    for tok in _NUM.findall(text):
        t = tok.replace(",", "").lstrip("-")
        try:
            # format(..., "f") keeps "80" as "80" (str() of a normalized Decimal gives "8E+1")
            out.add(format(Decimal(t).normalize(), "f"))
        except Exception:
            pass
    return out


def is_grounded(text: str, facts) -> tuple[bool, list[str]]:
    """True if every number in `text` already appears somewhere in `facts`."""
    allowed = numbers(json.dumps(facts, default=str))
    unknown = sorted(numbers(text) - allowed)
    return (not unknown, unknown)
