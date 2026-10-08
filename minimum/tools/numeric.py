"""Numeric answer checking with relative and absolute tolerance."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


@dataclass
class NumericResult:
    correct: bool
    parsed: float | None
    detail: str


def extract_number(text: str) -> float | None:
    """Pull the last number out of free text (learners write '≈ 3.2e5 J')."""
    text = text.replace("×10^", "e").replace("x10^", "e").replace("·10^", "e")
    m = _NUM.findall(text)
    if not m:
        return None
    try:
        return float(m[-1])
    except ValueError:
        return None


def check_numeric(answer: str, value: float, rel_tol: float = 1e-2, abs_tol: float = 0.0) -> NumericResult:
    parsed = extract_number(answer)
    if parsed is None:
        return NumericResult(False, None, "no number found in answer")
    ok = math.isclose(parsed, value, rel_tol=rel_tol, abs_tol=abs_tol)
    return NumericResult(ok, parsed, f"got {parsed}, expected {value} (rel_tol={rel_tol}, abs_tol={abs_tol})")
