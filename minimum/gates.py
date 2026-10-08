"""Mastery gates.

A gate is passed when every exam it lists has a passing attempt. There is no
time limit and no elimination: the learner stays in the phase until the gate
is passed. Attempts are spaced by a cooldown so the learner studies rather
than re-rolls the exam. Each attempt draws a fresh problem set.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from .content import CourseBundle
from .models import ExamSpec, Problem
from .store import Store


class GateError(Exception):
    pass


def phase_index(bundle: CourseBundle, phase_id: str) -> int:
    for i, p in enumerate(bundle.course.phases):
        if p.id == phase_id:
            return i
    raise KeyError(phase_id)


def gate_status(bundle: CourseBundle, store: Store, learner: str, gate_id: str) -> dict:
    gate = bundle.course.gate(gate_id)
    exams = {e: store.exam_passed(learner, e) for e in gate.exams}
    return {"gate": gate.id, "exams": exams, "passed": all(exams.values())}


def can_attempt(store: Store, learner: str, spec: ExamSpec, now: datetime | None = None) -> tuple[bool, str]:
    now = now or datetime.now(timezone.utc)
    attempts = store.exam_attempts(learner, spec.id)
    if any(a.passed for a in attempts):
        return False, "already passed"
    open_attempts = [a for a in attempts if a.finished_at is None]
    if open_attempts:
        return False, f"attempt {open_attempts[0].id} is still open"
    if attempts:
        last = attempts[-1]
        ready_at = (last.finished_at or last.started_at) + timedelta(days=spec.cooldown_days)
        if now < ready_at:
            return False, f"cooldown until {ready_at.date().isoformat()}"
    return True, "ok"


def draw_exam(bundle: CourseBundle, store: Store, learner: str, spec: ExamSpec, seed: int | None = None) -> list[Problem]:
    """Draw problems for an attempt, avoiding ones used in earlier attempts."""
    rng = random.Random(seed)
    pool = [
        p
        for p in bundle.problems.values()
        if p.module in spec.modules and p.gate_eligible and p.difficulty >= spec.min_difficulty
    ]
    used = {pid for a in store.exam_attempts(learner, spec.id) for pid in a.problem_ids}
    fresh = [p for p in pool if p.id not in used]
    chosen: list[Problem] = []
    # Spread across modules first, then fill.
    by_module: dict[str, list[Problem]] = {}
    for p in fresh:
        by_module.setdefault(p.module, []).append(p)
    for mod in spec.modules:
        if mod in by_module and len(chosen) < spec.n_problems:
            chosen.append(rng.choice(by_module[mod]))
    remaining = [p for p in fresh if p not in chosen]
    rng.shuffle(remaining)
    while len(chosen) < spec.n_problems and remaining:
        chosen.append(remaining.pop())
    if len(chosen) < spec.n_problems:
        # bank exhausted for fresh problems; allow reuse
        reuse = [p for p in pool if p not in chosen]
        rng.shuffle(reuse)
        while len(chosen) < spec.n_problems and reuse:
            chosen.append(reuse.pop())
    if len(chosen) < spec.n_problems:
        raise GateError(f"exam {spec.id}: bank too small ({len(chosen)} < {spec.n_problems})")
    return chosen


def try_advance(bundle: CourseBundle, store: Store, learner: str) -> tuple[bool, str]:
    """Advance to the next phase if the current phase's gate is passed."""
    rec = store.learner(learner)
    if rec is None:
        raise GateError("unknown learner")
    cur = rec["current_phase"]
    idx = phase_index(bundle, cur)
    gate = bundle.course.gate_after(cur)
    if gate is None:
        if idx == len(bundle.course.phases) - 1:
            return False, "final phase has no gate; completion is by defense"
        nxt = bundle.course.phases[idx + 1].id
        store.set_phase(learner, nxt)
        return True, nxt
    status = gate_status(bundle, store, learner, gate.id)
    if not status["passed"]:
        missing = [e for e, ok in status["exams"].items() if not ok]
        return False, f"gate {gate.id} not passed; outstanding exams: {', '.join(missing)}"
    if idx == len(bundle.course.phases) - 1:
        return False, "already in final phase"
    nxt = bundle.course.phases[idx + 1].id
    store.set_phase(learner, nxt)
    store.log(learner, "phase_advanced", {"from": cur, "to": nxt})
    return True, nxt
