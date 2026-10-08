"""The orchestrator: wiring, isolation, and the rules no prompt can enforce.

Three rules live here and nowhere else:

1. **Solution lock.** A reference solution is never shown, to the learner or
   to the tutor, until a submission for that problem is on record.
2. **Examiner isolation.** The examiner's view is built only from exam
   outcomes. Tutor, grader, and critic notes never reach it.
3. **Verification.** Every grade, every tutor reply, and every lecture review
   passes through the verifier before the learner sees it. A rejected output
   is re-run once with the verifier's issues attached, and the final grade
   records whether verification succeeded.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .agents import Advisor, Critic, Examiner, Grader, Lecturer, Pacer, Tutor, Verifier
from .agents.faculty import (
    AdvisorView,
    CriticView,
    ExaminerView,
    GraderView,
    LecturerView,
    PacerView,
    TutorView,
    VerifierView,
)
from .agents.schemas import (
    AdvisorReply,
    EdgeNoteOut,
    ExaminerTurn,
    GraderVerdict,
    LectureReview,
    LessonOut,
    PacingPlan,
    TutorReply,
    VerifierVerdict,
)
from .content import CourseBundle
from .gates import GateError, can_attempt, draw_exam, gate_status, try_advance
from .llm import ModelClient
from .models import (
    CodeAnswer,
    EdgeNote,
    ExamAttempt,
    Grade,
    NumericAnswer,
    Problem,
    RubricAnswer,
    Submission,
    SymbolicAnswer,
)
from .retrieval import new_card, retention_estimate, review
from .store import Store
from .tools import check_numeric, check_symbolic, run_code_tests


class SolutionLocked(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class OralSession:
    learner: str
    exam_id: str
    view: ExaminerView
    history: list[dict] = field(default_factory=list)
    asked: int = 0
    last: ExaminerTurn | None = None
    attempt_id: int | None = None

    @property
    def done(self) -> bool:
        return bool(self.last and self.last.done)


class Orchestrator:
    def __init__(self, bundle: CourseBundle, store: Store, client: ModelClient):
        self.bundle = bundle
        self.store = store
        self.tutor = Tutor(client)
        self.examiner = Examiner(client)
        self.grader = Grader(client)
        self.verifier = Verifier(client)
        self.pacer = Pacer(client)
        self.advisor = Advisor(client)
        self.critic = Critic(client)
        self.lecturer = Lecturer(client)
        self._lesson_locks: dict[tuple[str, str], threading.Lock] = {}
        self._lesson_locks_guard = threading.Lock()

    # --- enrolment and modules -------------------------------------------
    def enroll(self, learner: str) -> None:
        if self.store.learner(learner):
            raise ValueError(f"learner {learner} already enrolled")
        self.store.create_learner(learner, self.bundle.course.id, self.bundle.course.phases[0].id)
        self.store.log(learner, "enrolled", {"course": self.bundle.course.id})

    def start_module(self, learner: str, module_id: str) -> int:
        """Create retrieval cards for the module's concepts. Returns cards created."""
        mod = self.bundle.course.module(module_id)
        created = 0
        for i, concept in enumerate(mod.concepts):
            cid = f"{module_id}:c{i}"
            if self.store.card(learner, cid) is None:
                self.store.upsert_card(learner, new_card(cid, module_id, f"State and explain: {concept}"))
                created += 1
        self.store.log(learner, "module_started", {"module": module_id, "cards": created})
        self.store.set_current_module(learner, module_id)
        return created

    # --- lessons -----------------------------------------------------------
    def lecturer_view(self, module_id: str, concept: str) -> LecturerView:
        mod = self.bundle.course.module(module_id)
        return LecturerView(
            module_title=mod.title,
            module_summary=mod.summary,
            field=mod.field,
            concept=concept,
            all_concepts=mod.concepts,
            outcomes=mod.outcomes,
            texts=[f"{t.title} ({t.author}){': ' + t.note if t.note else ''}" for t in mod.texts],
            prerequisites=[self.bundle.course.module(p).title for p in mod.prerequisites],
        )

    def _lock_for(self, key: tuple[str, str]) -> threading.Lock:
        with self._lesson_locks_guard:
            return self._lesson_locks.setdefault(key, threading.Lock())

    def get_lesson(self, module_id: str, concept: str, regenerate: bool = False) -> dict:
        """Return the stored lesson, generating and verifying it on first request."""
        if not regenerate:
            existing = self.store.lesson(module_id, concept)
            if existing:
                return existing
        with self._lock_for((module_id, concept)):
            if not regenerate:
                existing = self.store.lesson(module_id, concept)
                if existing:
                    return existing
            view = self.lecturer_view(module_id, concept)
            lesson = self.lecturer.write(view)
            check = self._verify("lecturer", view.render(), lesson.model_dump_json(), None)
            if not check.approved and check.severity == "major":
                lesson = self.lecturer.write(view, fix="\n".join(f"- {i}" for i in check.issues))
                check = self._verify("lecturer", view.render(), lesson.model_dump_json(), None)
            self.store.save_lesson(module_id, concept, lesson.model_dump(), check.approved, "; ".join(check.issues))
            return self.store.lesson(module_id, concept)

    def pregenerate_lessons(self, module_id: str) -> threading.Thread:
        """Generate every missing lesson for a module in the background."""
        mod = self.bundle.course.module(module_id)

        def run():
            for c in mod.concepts:
                try:
                    self.get_lesson(module_id, c)
                except Exception:  # noqa: BLE001 - background work must never crash the app
                    pass

        t = threading.Thread(target=run, daemon=True)
        t.start()
        return t

    def check_lesson_answer(self, learner: str, module_id: str, concept: str, answer: str) -> TutorReply:
        """The tutor judges the learner's answer to the lesson's check question."""
        lesson = self.get_lesson(module_id, concept)
        body = lesson["body"]
        view = self.tutor_view(learner, module_id, None)
        prompt = (
            f"The learner has just studied the lesson on '{concept}' and is answering its check question.\n"
            f"QUESTION: {body['check_question']}\n"
            f"WHAT A CORRECT ANSWER MUST CONTAIN: {body['check_answer_outline']}\n\n"
            f"LEARNER'S ANSWER:\n{answer}\n\n"
            "Say whether it is correct. If not, say exactly what is missing or wrong and ask one question that would lead them to it. Record an edge note if it reveals a gap."
        )
        reply = self.tutor.reply(view, [], prompt)
        self._record_edges(learner, module_id, reply.edge_notes, "tutor")
        return reply

    def mark_lesson_done(self, learner: str, module_id: str, concept: str) -> None:
        self.store.log(learner, "lesson_done", {"module": module_id, "concept": concept})

    # --- problems ----------------------------------------------------------
    def problem(self, problem_id: str) -> Problem:
        if problem_id in self.bundle.problems:
            return self.bundle.problems[problem_id]
        for ex in self.bundle.fixed_exams.values():
            for p in ex.problems:
                if p.id == problem_id:
                    return p
        raise KeyError(problem_id)

    def reveal_solution(self, learner: str, problem_id: str) -> str:
        if not self.store.has_submitted(learner, problem_id):
            raise SolutionLocked(f"solution for {problem_id} unlocks after you submit an attempt")
        return self.problem(problem_id).reference_solution

    # --- verification helper ----------------------------------------------
    def _verify(self, role: str, inputs: str, output: str, reference: str | None) -> VerifierVerdict:
        return self.verifier.check(VerifierView(role_under_review=role, inputs=inputs, output=output, reference_solution=reference))

    def _record_edges(self, learner: str, module: str, notes: list[EdgeNoteOut], source: str) -> None:
        for n in notes:
            self.store.add_edge_note(EdgeNote(learner=learner, module=module, concept=n.concept, note=n.note, source=source, created_at=_now()))
            cid = f"{module}:edge:{abs(hash(n.concept)) % 10**8}"
            if self.store.card(learner, cid) is None:
                self.store.upsert_card(learner, new_card(cid, module, f"Edge note: {n.concept} — {n.note}"))

    # --- tutoring ----------------------------------------------------------
    def tutor_view(self, learner: str, module_id: str, problem_id: str | None) -> TutorView:
        mod = self.bundle.course.module(module_id)
        statement = None
        attempts: list[str] = []
        if problem_id:
            statement = self.problem(problem_id).statement
            attempts = [s.content[:1500] for s in self.store.submissions(learner, problem_id)]
        notes = [f"{n.concept}: {n.note}" for n in self.store.edge_notes(learner, module_id, sources=("tutor",))]
        return TutorView(
            module_title=mod.title,
            module_summary=mod.summary,
            outcomes=mod.outcomes,
            texts=[f"{t.title} ({t.author}){': ' + t.note if t.note else ''}" for t in mod.texts],
            problem_statement=statement,
            prior_attempts=attempts,
            tutor_edge_notes=notes,
        )

    def tutor_turn(self, learner: str, module_id: str, history: list[dict], message: str, problem_id: str | None = None) -> tuple[TutorReply, bool]:
        view = self.tutor_view(learner, module_id, problem_id)
        reply = self.tutor.reply(view, history, message)
        reference = self.problem(problem_id).reference_solution if problem_id else None
        verdict = self._verify("tutor", view.render() + "\n\nLEARNER SAID:\n" + message, reply.model_dump_json(), reference)
        if reply.revealed_solution or (not verdict.approved and verdict.severity == "major"):
            fix = "VERIFIER FOUND PROBLEMS WITH YOUR LAST REPLY. Rewrite it, fixing these:\n" + "\n".join(f"- {i}" for i in verdict.issues)
            if reply.revealed_solution:
                fix += "\n- You revealed the solution to an unsubmitted problem. Do not."
            reply = self.tutor.reply(view, history + [self.tutor.user(message), self.tutor.assistant(reply.reply)], fix)
            verdict = self._verify("tutor", view.render(), reply.model_dump_json(), reference)
        if reply.revealed_solution:
            # Hard stop: never show a reply the tutor itself flags.
            reply = TutorReply(reply="Solutions unlock after you submit an attempt. Submit what you have and we will go from there.", edge_notes=reply.edge_notes, revealed_solution=False)
        self._record_edges(learner, module_id, reply.edge_notes, "tutor")
        self.store.log(learner, "tutor_turn", {"module": module_id, "problem": problem_id, "verified": verdict.approved})
        return reply, verdict.approved

    # --- persistent chats (tutor, advisor) ----------------------------------
    def chat_turn(self, learner: str, key: str, module_id: str | None, message: str, problem_id: str | None = None):
        """One turn of a stored conversation. key is 'tutor:<module>:<problem>' or 'advisor'."""
        history = self.store.chat(learner, key)
        if key == "advisor":
            r = self.advise(learner, history, message)
            text = r.reply + "".join(f"\n- critique: {x}" for x in r.critique) + "".join(f"\n- next: {x}" for x in r.next_actions)
        else:
            reply, ok = self.tutor_turn(learner, module_id, history, message, problem_id)
            text = reply.reply + ("" if ok else "  [unverified]")
        self.store.add_chat(learner, key, "user", message)
        self.store.add_chat(learner, key, "assistant", text)
        return text

    # --- grading -----------------------------------------------------------
    def _deterministic(self, p: Problem, content: str) -> tuple[float | None, str | None]:
        """Return (points awarded for the final answer, description) or (None, None)."""
        a = p.answer
        if isinstance(a, SymbolicAnswer):
            final = _final_answer(content)
            r = check_symbolic(final, a.expr, a.symbols)
            return (p.points if r.equivalent else 0.0), f"symbolic check on '{final}': {'EQUIVALENT' if r.equivalent else 'NOT equivalent'} ({r.detail})"
        if isinstance(a, NumericAnswer):
            final = _final_answer(content)
            r = check_numeric(final, a.value, a.rel_tol, a.abs_tol)
            return (p.points if r.correct else 0.0), f"numeric check on '{final}': {'CORRECT' if r.correct else 'WRONG'} ({r.detail})"
        if isinstance(a, CodeAnswer):
            r = run_code_tests(content, a.entry_point, a.tests, a.timeout_s)
            frac = r.passed / r.total if r.total else 0.0
            desc = f"tests: {r.passed}/{r.total} passed" + (f"; error: {r.error}" if r.error else "")
            return round(p.points * frac, 2), desc
        return None, None

    def submit(self, learner: str, problem_id: str, content: str, exam_attempt_id: int | None = None) -> Grade:
        p = self.problem(problem_id)
        sub_id = self.store.add_submission(Submission(learner=learner, problem_id=problem_id, content=content, submitted_at=_now(), exam_attempt_id=exam_attempt_id))
        det_points, det_desc = self._deterministic(p, content)
        rubric = p.grading_rubric
        breakdown: list[dict] = []
        feedback_parts: list[str] = []
        verified = True
        notes = ""
        score = det_points or 0.0
        if det_desc:
            breakdown.append({"criterion": "final answer", "points_possible": p.points, "points_awarded": det_points, "justification": det_desc})
            feedback_parts.append(det_desc)
        if rubric:
            view = GraderView(
                problem_statement=p.statement,
                reference_solution=p.reference_solution,
                rubric=[r.model_dump() for r in rubric],
                submission=content,
                deterministic_result=det_desc,
            )
            verdict = self.grader.grade(view)
            check = self._verify("grader", view.render(), verdict.model_dump_json(), p.reference_solution)
            if not check.approved and check.severity == "major":
                retry_view = view.model_copy(update={"submission": content + "\n\n[VERIFIER NOTES ON A PREVIOUS GRADING ATTEMPT; FIX THESE]\n" + "\n".join(check.issues)})
                verdict = self.grader.grade(retry_view)
                check = self._verify("grader", view.render(), verdict.model_dump_json(), p.reference_solution)
            verified = check.approved
            notes = "; ".join(check.issues)
            possible = {r.criterion: r.points for r in rubric}
            for rs in verdict.rubric_scores:
                cap = possible.get(rs.criterion, rs.points_possible)
                awarded = max(0, min(rs.points_awarded, cap))
                score += awarded
                breakdown.append({"criterion": rs.criterion, "points_possible": cap, "points_awarded": awarded, "justification": rs.justification})
            feedback_parts.append(verdict.feedback)
            self._record_edges(learner, p.module, verdict.edge_notes, "grader")
        grade = Grade(
            submission_id=sub_id,
            score=min(score, p.max_score),
            max_score=p.max_score,
            deterministic=det_desc is not None and not rubric,
            feedback="\n\n".join(feedback_parts),
            rubric_breakdown=breakdown,
            verified=verified,
            verifier_notes=notes,
            graded_at=_now(),
        )
        self.store.add_grade(grade)
        self.store.log(learner, "graded", {"problem": problem_id, "score": grade.score, "max": grade.max_score, "verified": verified})
        return grade

    # --- written exams -----------------------------------------------------
    def start_written(self, learner: str, exam_id: str, seed: int | None = None) -> tuple[int, list[Problem]]:
        if exam_id in self.bundle.fixed_exams:
            fx = self.bundle.fixed_exams[exam_id]
            problems = list(fx.problems)
        else:
            spec = self.bundle.course.exam(exam_id)
            if spec.oral:
                raise GateError(f"{exam_id} is an oral exam; use start_oral")
            ok, why = can_attempt(self.store, learner, spec)
            if not ok:
                raise GateError(f"cannot attempt {exam_id}: {why}")
            problems = draw_exam(self.bundle, self.store, learner, spec, seed)
        att = ExamAttempt(learner=learner, exam_id=exam_id, problem_ids=[p.id for p in problems], started_at=_now())
        att_id = self.store.start_exam(att)
        self.store.log(learner, "exam_started", {"exam": exam_id, "attempt": att_id})
        return att_id, problems

    def submit_written(self, learner: str, attempt_id: int, answers: dict[str, str]) -> ExamAttempt:
        attempts = [a for a in self.store.exam_attempts(learner) if a.id == attempt_id]
        if not attempts:
            raise GateError(f"no attempt {attempt_id}")
        att = attempts[0]
        if att.finished_at is not None:
            raise GateError(f"attempt {attempt_id} already finished")
        pass_mark = self.bundle.fixed_exams[att.exam_id].pass_mark if att.exam_id in self.bundle.fixed_exams else self.bundle.course.exam(att.exam_id).pass_mark
        score = 0.0
        max_score = 0.0
        for pid in att.problem_ids:
            p = self.problem(pid)
            max_score += p.max_score
            content = answers.get(pid, "")
            if content.strip():
                g = self.submit(learner, pid, content, exam_attempt_id=attempt_id)
                score += g.score
            else:
                self.store.add_submission(Submission(learner=learner, problem_id=pid, content="", submitted_at=_now(), exam_attempt_id=attempt_id))
        passed = max_score > 0 and (score / max_score) >= pass_mark
        self.store.finish_exam(attempt_id, score, max_score, passed)
        self.store.log(learner, "exam_finished", {"exam": att.exam_id, "attempt": attempt_id, "score": score, "max": max_score, "passed": passed})
        return [a for a in self.store.exam_attempts(learner) if a.id == attempt_id][0]

    # --- oral exams --------------------------------------------------------
    def examiner_view(self, learner: str, exam_id: str) -> ExaminerView:
        spec = self.bundle.course.exam(exam_id)
        mods = [self.bundle.course.module(m) for m in spec.modules]
        concepts = [c for m in mods for c in m.concepts]
        record = []
        for a in self.store.exam_attempts(learner):
            if a.finished_at is not None:
                record.append(f"{a.exam_id}: {'pass' if a.passed else 'fail'} ({a.score}/{a.max_score})" if a.max_score else f"{a.exam_id}: {'pass' if a.passed else 'fail'}")
        return ExaminerView(
            exam_title=spec.title,
            standard=spec.standard or f"pass mark {spec.pass_mark:.0%}",
            modules=[m.title for m in mods],
            concepts=concepts,
            past_exam_record=record,
            max_questions=spec.max_questions,
        )

    def start_oral(self, learner: str, exam_id: str) -> OralSession:
        spec = self.bundle.course.exam(exam_id)
        if not spec.oral:
            raise GateError(f"{exam_id} is a written exam")
        ok, why = can_attempt(self.store, learner, spec)
        if not ok:
            raise GateError(f"cannot attempt {exam_id}: {why}")
        view = self.examiner_view(learner, exam_id)
        att_id = self.store.start_exam(ExamAttempt(learner=learner, exam_id=exam_id, problem_ids=[], started_at=_now()))
        sess = OralSession(learner=learner, exam_id=exam_id, view=view, attempt_id=att_id)
        turn = self.examiner.open(view)
        sess.last = turn
        sess.asked = 1
        sess.history.append(self.examiner.assistant(turn.question))
        self._save_oral(sess)
        return sess

    def _save_oral(self, sess: OralSession) -> None:
        self.store.save_oral(sess.attempt_id, sess.learner, sess.exam_id, {
            "view": sess.view.model_dump(), "history": sess.history, "asked": sess.asked,
            "last": sess.last.model_dump() if sess.last else None,
        })

    def resume_oral(self, learner: str, exam_id: str) -> OralSession | None:
        """The open oral session for this exam, if one survives in the store."""
        found = self.store.load_oral(learner, exam_id)
        if not found:
            return None
        att_id, state = found
        att = next((a for a in self.store.exam_attempts(learner, exam_id) if a.id == att_id), None)
        if att is None or att.finished_at is not None:
            self.store.delete_oral(att_id)
            return None
        return OralSession(learner=learner, exam_id=exam_id, view=ExaminerView.model_validate(state["view"]),
                           history=state["history"], asked=state["asked"],
                           last=ExaminerTurn.model_validate(state["last"]) if state.get("last") else None, attempt_id=att_id)

    def oral_answer(self, sess: OralSession, answer: str) -> ExaminerTurn:
        if sess.done:
            raise GateError("exam is over")
        turn = self.examiner.step(sess.view, sess.history, answer, sess.asked)
        sess.history.append(self.examiner.user(answer))
        if turn.done or sess.asked >= sess.view.max_questions:
            if not turn.done:
                turn = turn.model_copy(update={"done": True, "verdict": turn.verdict or "fail", "assessment": (turn.assessment or "") + " [question limit reached]"})
            passed = (turn.verdict or "").lower().startswith("pass")
            self.store.finish_exam(sess.attempt_id, 1.0 if passed else 0.0, 1.0, passed)
            spec = self.bundle.course.exam(sess.exam_id)
            for mid in spec.modules:
                self._record_edges(sess.learner, mid, turn.edge_notes, "examiner")
            self.store.log(sess.learner, "exam_finished", {"exam": sess.exam_id, "attempt": sess.attempt_id, "passed": passed, "level": turn.level_reached})
            self.store.delete_oral(sess.attempt_id)
            sess.last = turn
            return turn
        sess.asked += 1
        sess.history.append(self.examiner.assistant(turn.question))
        sess.last = turn
        self._save_oral(sess)
        return turn

    # --- gates -------------------------------------------------------------
    def advance(self, learner: str) -> tuple[bool, str]:
        return try_advance(self.bundle, self.store, learner)

    # --- retrieval ---------------------------------------------------------
    def due(self, learner: str) -> list[dict]:
        return self.store.due_cards(learner)

    def review_card(self, learner: str, card_id: str, grade: int) -> dict:
        card = self.store.card(learner, card_id)
        if card is None:
            raise KeyError(card_id)
        updated = review(card, grade)
        self.store.upsert_card(learner, updated)
        self.store.log(learner, "review", {"card": card_id, "grade": grade})
        return updated

    # --- pacing ------------------------------------------------------------
    def pacer_view(self, learner: str) -> PacerView:
        rec = self.store.learner(learner)
        if rec is None:
            raise KeyError(learner)
        phase = next(p for p in self.bundle.course.phases if p.id == rec["current_phase"])
        last = self.store.last_activity(learner)
        idle = (_now() - last).days if last else 0
        cards = self.store.cards(learner)
        grades = []
        for s in self.store.submissions(learner)[-10:]:
            g = self.store.grade(s.id)
            if g:
                grades.append(f"{s.problem_id}: {g.score}/{g.max_score}")
        record = [f"{a.exam_id}: {'pass' if a.passed else 'fail' if a.finished_at else 'open'}" for a in self.store.exam_attempts(learner)]
        edges = [f"[{n.source}] {n.module} / {n.concept}: {n.note}" for n in self.store.edge_notes(learner)][-30:]
        gate = self.bundle.course.gate_after(phase.id)
        gstat = gate_status(self.bundle, self.store, learner, gate.id) if gate else {"passed": False, "exams": {}}
        return PacerView(
            phase=f"{phase.id} {phase.title}",
            modules_in_phase=[f"{m.id} {m.title}" for m in phase.modules],
            daily_blocks=[f"{b.name} ({b.hours}h): {b.description}" for b in self.bundle.course.daily_blocks],
            days_idle=idle,
            retention=retention_estimate(cards),
            due_cards=len(self.store.due_cards(learner)),
            recent_grades=grades,
            exam_record=record,
            edge_notes=edges,
            gate_status=f"{gate.id}: {gstat['exams']}" if gate else "none",
        )

    def plan(self, learner: str) -> PacingPlan:
        plan = self.pacer.plan(self.pacer_view(learner))
        self.store.log(learner, "plan", {"blocks": len(plan.blocks), "drift": plan.drift_risk})
        return plan

    # --- advisor and critic ------------------------------------------------
    def advise(self, learner: str, history: list[dict], message: str) -> AdvisorReply:
        rec = self.store.learner(learner)
        phase = next(p for p in self.bundle.course.phases if p.id == rec["current_phase"])
        notes = [e["payload"].get("note", "") for e in self.store.events(learner, "project_note")]
        view = AdvisorView(anchors=rec["anchors"], weeks_remaining=phase.weeks, project_notes=notes)
        reply = self.advisor.reply(view, history, message)
        self.store.log(learner, "advisor_turn", {"n": len(history)})
        return reply

    def critique(self, learner: str, module_id: str, lecture: str) -> tuple[LectureReview, bool]:
        mod = self.bundle.course.module(module_id)
        view = CriticView(module_title=mod.title, outcomes=mod.outcomes, lecture=lecture)
        rev = self.critic.review(view)
        check = self._verify("critic", view.render(), rev.model_dump_json(), None)
        if not check.approved and check.severity == "major":
            rev = self.critic.review(view.model_copy(update={"lecture": lecture + "\n\n[VERIFIER NOTES ON A PREVIOUS REVIEW; FIX THESE]\n" + "\n".join(check.issues)}))
            check = self._verify("critic", view.render(), rev.model_dump_json(), None)
        self._record_edges(learner, module_id, rev.edge_notes, "critic")
        self.store.log(learner, "lecture_reviewed", {"module": module_id, "correctness": rev.correctness, "clarity": rev.clarity})
        return rev, check.approved

    # --- status ------------------------------------------------------------
    def status(self, learner: str) -> dict:
        rec = self.store.learner(learner)
        if rec is None:
            raise KeyError(learner)
        phase = next(p for p in self.bundle.course.phases if p.id == rec["current_phase"])
        gate = self.bundle.course.gate_after(phase.id)
        out = {
            "learner": learner,
            "course": self.bundle.course.title,
            "phase": f"{phase.id} {phase.title}",
            "anchors": rec["anchors"],
            "started": rec["started_at"].date().isoformat(),
            "cards": len(self.store.cards(learner)),
            "due": len(self.store.due_cards(learner)),
            "retention": round(retention_estimate(self.store.cards(learner)), 2),
            "submissions": len(self.store.submissions(learner)),
            "gate": gate_status(self.bundle, self.store, learner, gate.id) if gate else None,
            "edge_notes": len(self.store.edge_notes(learner)),
        }
        return out


def _final_answer(content: str) -> str:
    """The learner's final answer: the last line starting 'ANSWER:' or the last non-empty line."""
    lines = [ln.strip() for ln in content.strip().splitlines() if ln.strip()]
    for ln in reversed(lines):
        if ln.upper().startswith("ANSWER:"):
            return ln.split(":", 1)[1].strip()
    return lines[-1] if lines else ""
