from datetime import datetime, timedelta, timezone

from minimum.day import build_day, load_or_build_day, mark_done, next_concept, next_problem


def test_day_order_and_module_selection(orch):
    steps = build_day(orch.bundle, orch.store, "tess")
    kinds = [s.kind for s in steps]
    assert kinds[0] == "lesson"  # no cards yet, so no retrieval step
    assert "reading" in kinds and "problem" in kinds and "stretch" in kinds and "lecture" in kinds
    assert orch.store.learner("tess")["current_module"] == "p1.analysis"


def test_retrieval_first_when_cards_due(orch):
    orch.start_module("tess", "p1.analysis")
    steps = build_day(orch.bundle, orch.store, "tess")
    assert steps[0].kind == "retrieval" and "6 cards" in steps[0].title


def test_lesson_advances_after_studied(orch):
    assert next_concept(orch.bundle, orch.store, "tess", "p1.analysis")[0] == 0
    orch.mark_lesson_done("tess", "p1.analysis", "epsilon-delta definition of a limit")
    assert next_concept(orch.bundle, orch.store, "tess", "p1.analysis")[0] == 1
    for c in orch.bundle.course.module("p1.analysis").concepts:
        orch.mark_lesson_done("tess", "p1.analysis", c)
    assert next_concept(orch.bundle, orch.store, "tess", "p1.analysis") is None
    assert not any(s.kind == "lesson" for s in build_day(orch.bundle, orch.store, "tess"))


def test_next_problem_prefers_unsubmitted_then_weakest(orch, script):
    p, why = next_problem(orch.bundle, orch.store, "tess", "p1.ode")
    assert p.difficulty == 3 and why == "first attempt"
    script.grader_fraction = 0.0
    for pr in orch.bundle.problems_for("p1.ode"):
        orch.submit("tess", pr.id, "ANSWER: 0")
    p, why = next_problem(orch.bundle, orch.store, "tess", "p1.ode")
    assert p is not None and "below the 70% bar" in why


def test_day_persists_done_and_rebuilds(orch):
    steps, done = load_or_build_day(orch.bundle, orch.store, "tess")
    assert done == set()
    mark_done(orch.store, "tess", steps[0].id)
    steps2, done2 = load_or_build_day(orch.bundle, orch.store, "tess")
    assert steps[0].id in done2
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    _, done3 = load_or_build_day(orch.bundle, orch.store, "tess", now=tomorrow)
    assert done3 == set()


def test_weekly_lecture_only_once_a_week(orch):
    assert any(s.kind == "lecture" for s in build_day(orch.bundle, orch.store, "tess"))
    orch.critique("tess", "p1.linalg", "a lecture")
    assert not any(s.kind == "lecture" for s in build_day(orch.bundle, orch.store, "tess"))
