from datetime import datetime, timedelta, timezone

from minimum.retrieval import MIN_EASE, new_card, retention_estimate, review


def test_new_card_due_now():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c = new_card("a", "m", "p", now)
    assert c["due_at"] == now and c["repetitions"] == 0


def test_good_reviews_grow_interval():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c = new_card("a", "m", "p", now)
    c = review(c, 4, now)
    assert c["interval_days"] == 1.0
    c = review(c, 4, now + timedelta(days=1))
    assert c["interval_days"] == 3.0
    c = review(c, 5, now + timedelta(days=4))
    assert c["interval_days"] > 3.0 and c["repetitions"] == 3


def test_lapse_resets_and_ease_floors():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    c = new_card("a", "m", "p", now)
    for _ in range(20):
        c = review(c, 0, now)
    assert c["repetitions"] == 0 and c["interval_days"] == 0.0
    assert c["ease"] == MIN_EASE
    assert c["due_at"] - now < timedelta(hours=1)


def test_retention_estimate():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    fresh = review(new_card("a", "m", "p", now), 4, now)
    stale = dict(fresh, due_at=now - timedelta(days=30), interval_days=1.0)
    assert retention_estimate([fresh], now + timedelta(hours=1)) == 1.0
    assert retention_estimate([fresh, stale], now + timedelta(hours=1)) == 0.5
    assert retention_estimate([], now) == 1.0
