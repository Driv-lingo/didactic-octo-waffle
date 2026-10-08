"""Spaced retrieval scheduler.

An SM-2 style scheduler over "cards". A card is anything the learner must
be able to reproduce cold: a definition, a derivation step, a formula, a
mini-problem. Cards are created when a module is started and when a grader
or examiner records an edge note (the thing that broke becomes a card).

Grades are 0..5: 0-2 is a lapse, 3 is hard, 4 is good, 5 is easy.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

MIN_EASE = 1.3
DEFAULT_EASE = 2.5


def new_card(card_id: str, module: str, prompt: str, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    return {
        "card_id": card_id,
        "module": module,
        "prompt": prompt,
        "ease": DEFAULT_EASE,
        "interval_days": 0.0,
        "repetitions": 0,
        "due_at": now,
        "last_grade": None,
    }


def review(card: dict, grade: int, now: datetime | None = None) -> dict:
    """Return an updated copy of ``card`` after a review graded 0..5."""
    if not 0 <= grade <= 5:
        raise ValueError("grade must be 0..5")
    now = now or datetime.now(timezone.utc)
    c = dict(card)
    if grade < 3:
        c["repetitions"] = 0
        c["interval_days"] = 0.0
        due = now + timedelta(minutes=10)  # again today
    else:
        reps = c["repetitions"]
        if reps == 0:
            interval = 1.0
        elif reps == 1:
            interval = 3.0
        else:
            interval = c["interval_days"] * c["ease"]
        c["repetitions"] = reps + 1
        c["interval_days"] = interval
        due = now + timedelta(days=interval)
    ease = c["ease"] + (0.1 - (5 - grade) * (0.08 + (5 - grade) * 0.02))
    c["ease"] = max(MIN_EASE, ease)
    c["due_at"] = due
    c["last_grade"] = grade
    return c


def retention_estimate(cards: list[dict], now: datetime | None = None) -> float:
    """Crude fraction of cards believed retained: not overdue, and not lapsed."""
    if not cards:
        return 1.0
    now = now or datetime.now(timezone.utc)
    ok = 0
    for c in cards:
        overdue_days = (now - c["due_at"]).total_seconds() / 86400
        if c["repetitions"] > 0 and overdue_days <= max(1.0, c["interval_days"] * 0.5):
            ok += 1
    return ok / len(cards)
