from datetime import datetime, timedelta, timezone

import pytest

from minimum.gates import GateError, can_attempt, draw_exam, try_advance
from minimum.models import ExamAttempt
from minimum.store import Store


@pytest.fixture
def store(bundle):
    s = Store(":memory:")
    s.create_learner("tess", bundle.course.id, "p1")
    return s


def test_draw_prefers_fresh_problems_and_spreads_modules(bundle, store):
    spec = bundle.course.exam("gate1.written")
    first = draw_exam(bundle, store, "tess", spec, seed=1)
    assert len(first) == spec.n_problems
    assert len({p.module for p in first}) >= 5
    assert all(p.difficulty >= spec.min_difficulty for p in first)
    att = ExamAttempt(learner="tess", exam_id=spec.id, problem_ids=[p.id for p in first], started_at=datetime.now(timezone.utc))
    aid = store.start_exam(att)
    store.finish_exam(aid, 0, 1, False)
    second = draw_exam(bundle, store, "tess", spec, seed=2)
    assert not ({p.id for p in first} & {p.id for p in second})


def test_cooldown_blocks_retry(bundle, store):
    spec = bundle.course.exam("gate1.written")
    assert can_attempt(store, "tess", spec)[0]
    aid = store.start_exam(ExamAttempt(learner="tess", exam_id=spec.id, problem_ids=[], started_at=datetime.now(timezone.utc)))
    ok, why = can_attempt(store, "tess", spec)
    assert not ok and "still open" in why
    store.finish_exam(aid, 0, 1, False)
    ok, why = can_attempt(store, "tess", spec)
    assert not ok and "cooldown" in why
    later = datetime.now(timezone.utc) + timedelta(days=spec.cooldown_days + 1)
    assert can_attempt(store, "tess", spec, now=later)[0]


def test_passed_exam_cannot_be_retaken(bundle, store):
    spec = bundle.course.exam("gate1.written")
    aid = store.start_exam(ExamAttempt(learner="tess", exam_id=spec.id, problem_ids=[], started_at=datetime.now(timezone.utc)))
    store.finish_exam(aid, 1, 1, True)
    ok, why = can_attempt(store, "tess", spec)
    assert not ok and "already passed" in why


def test_advance_requires_both_gate_exams(bundle, store):
    ok, msg = try_advance(bundle, store, "tess")
    assert not ok and "gate1" in msg
    for exam_id in ["gate1.written"]:
        aid = store.start_exam(ExamAttempt(learner="tess", exam_id=exam_id, problem_ids=[], started_at=datetime.now(timezone.utc)))
        store.finish_exam(aid, 1, 1, True)
    ok, msg = try_advance(bundle, store, "tess")
    assert not ok and "gate1.oral" in msg
    aid = store.start_exam(ExamAttempt(learner="tess", exam_id="gate1.oral", problem_ids=[], started_at=datetime.now(timezone.utc)))
    store.finish_exam(aid, 1, 1, True)
    ok, msg = try_advance(bundle, store, "tess")
    assert ok and msg == "p2"
    assert store.learner("tess")["current_phase"] == "p2"


def test_bank_too_small_raises(bundle, store):
    spec = bundle.course.exam("gate1.written").model_copy(update={"n_problems": 10_000})
    with pytest.raises(GateError):
        draw_exam(bundle, store, "tess", spec)
