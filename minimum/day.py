"""The guided day.

A deterministic builder turns the learner's state into an ordered list of
steps for today: retrieval first, then the next lesson in the current
module, the reading it points to, the next problem, the module's project
or experiment, a stretch item, and once a week the teach-back lecture.
No model call is needed to build the day; the pacing agent's commentary
is layered on top by the orchestrator when the learner asks for it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pydantic import BaseModel

from .content import CourseBundle
from .store import Store


class Step(BaseModel):
    id: str
    kind: str  # retrieval | lesson | reading | problem | project | stretch | lecture | gate
    title: str
    detail: str
    url: str
    minutes: int


def today_key(now: datetime | None = None) -> str:
    return (now or datetime.now(timezone.utc)).date().isoformat()


def current_module(bundle: CourseBundle, store: Store, learner: str) -> str | None:
    rec = store.learner(learner)
    if rec is None:
        return None
    if rec.get("current_module"):
        return rec["current_module"]
    phase = next((p for p in bundle.course.phases if p.id == rec["current_phase"]), None)
    if not phase or not phase.modules:
        return None
    mid = phase.modules[0].id
    store.set_current_module(learner, mid)
    return mid


def next_concept(bundle: CourseBundle, store: Store, learner: str, module_id: str) -> tuple[int, str] | None:
    """The first concept in the module the learner has not marked studied."""
    mod = bundle.course.module(module_id)
    studied = {e["payload"].get("concept") for e in store.events(learner, "lesson_done", limit=1000) if e["payload"].get("module") == module_id}
    for i, c in enumerate(mod.concepts):
        if c not in studied:
            return i, c
    return None


def next_problem(bundle: CourseBundle, store: Store, learner: str, module_id: str):
    probs = bundle.problems_for(module_id)
    probs.sort(key=lambda p: (p.difficulty, p.id))
    unsubmitted = [p for p in probs if not store.has_submitted(learner, p.id)]
    if unsubmitted:
        return unsubmitted[0], "first attempt"
    # everything submitted: revisit the weakest
    weakest = None
    weakest_frac = 1.01
    for p in probs:
        best = 0.0
        for pid, g in store.grades_for_module(learner, [p.id]):
            best = max(best, g.score / g.max_score if g.max_score else 0)
        if best < weakest_frac:
            weakest, weakest_frac = p, best
    if weakest is not None and weakest_frac < 0.7:
        return weakest, f"best score so far {weakest_frac:.0%}; below the 70% bar"
    return None, "module problems complete"


def build_day(bundle: CourseBundle, store: Store, learner: str, now: datetime | None = None) -> list[Step]:
    now = now or datetime.now(timezone.utc)
    steps: list[Step] = []
    mid = current_module(bundle, store, learner)
    due = len(store.due_cards(learner, now))
    if due:
        steps.append(Step(id="retrieval", kind="retrieval", title=f"Retrieval: {due} cards due",
                          detail="Overdue cards first, always. Answer out loud or on paper, then grade yourself honestly.",
                          url="/review", minutes=max(10, min(40, 2 * due))))
    if mid is None:
        return steps
    mod = bundle.course.module(mid)
    nc = next_concept(bundle, store, learner, mid)
    if nc is not None:
        i, concept = nc
        lesson = store.lesson(mid, concept)
        section = lesson["body"].get("textbook_section") if lesson else None
        steps.append(Step(id=f"lesson:{i}", kind="lesson", title=f"Lesson: {concept}",
                          detail=f"Concept {i + 1} of {len(mod.concepts)} in {mod.title}. Read the lesson, work both examples on paper, answer the check question.",
                          url=f"/lesson/{mid}/{i}", minutes=45))
        primary = mod.texts[0] if mod.texts else None
        if primary:
            steps.append(Step(id=f"reading:{i}", kind="reading",
                              title=f"Read the text: {primary.title}" + (f", {section}" if section else ""),
                              detail="The lesson is the on-ramp; the text is the authority. Read the section the lesson names, with pen and paper, and reproduce every derivation.",
                              url=f"/lesson/{mid}/{i}#reading", minutes=60))
    prob, why = next_problem(bundle, store, learner, mid)
    if prob is not None:
        steps.append(Step(id=f"problem:{prob.id}", kind="problem", title=f"Problem {prob.id} (difficulty {prob.difficulty})",
                          detail=f"{why}. Submit whatever you have; the solution unlocks on submission and the grader tells you exactly what was missing.",
                          url=f"/problem/{prob.id}", minutes=60))
    if mod.project:
        steps.append(Step(id=f"project:{mid}", kind="project", title="Project or experiment",
                          detail=mod.project, url=f"/module/{mid}#project", minutes=30))
    stretch = next((m for m in mod.materials if m.kind == "stretch"), None)
    if stretch:
        steps.append(Step(id=f"stretch:{mid}", kind="stretch", title=f"Read above your level: {stretch.title}",
                          detail=(stretch.note or "") + " Three lines when done: its claim, its method, its weakest point.",
                          url=stretch.url, minutes=30))
    else:
        steps.append(Step(id=f"stretch:{mid}", kind="stretch", title="Read above your level",
                          detail="A paper or chapter slightly too hard for you, in any of the eight fields. Three lines when done: its claim, its method, its weakest point.",
                          url=f"/module/{mid}#materials", minutes=30))
    last_lecture = next((e["at"] for e in store.events(learner, "lecture_reviewed", limit=1)), None)
    if last_lecture is None or now - last_lecture > timedelta(days=7):
        steps.append(Step(id="lecture", kind="lecture", title="Weekly teach-back",
                          detail="Write a lecture on something you learned at least a week ago, as if teaching a strong student. The critic grades correctness first.",
                          url="/lecture", minutes=45))
    return steps


def load_or_build_day(bundle: CourseBundle, store: Store, learner: str, now: datetime | None = None) -> tuple[list[Step], set[str]]:
    key = today_key(now)
    saved = store.day(learner, key)
    steps = build_day(bundle, store, learner, now)
    done = set(saved["done"]) if saved else set()
    if saved is None or [s["id"] for s in saved["steps"]] != [s.id for s in steps]:
        store.save_day(learner, key, [s.model_dump() for s in steps], sorted(done & {s.id for s in steps}))
    return steps, done


def mark_done(store: Store, learner: str, step_id: str, now: datetime | None = None) -> None:
    key = today_key(now)
    saved = store.day(learner, key) or {"steps": [], "done": []}
    done = set(saved["done"]) | {step_id}
    store.save_day(learner, key, saved["steps"], sorted(done))
    store.log(learner, "step_done", {"step": step_id})
