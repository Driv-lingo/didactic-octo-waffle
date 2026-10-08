"""The seven faculty roles.

Each ``*View`` is the complete set of facts the role is allowed to see. The
orchestrator builds the view; the agent renders it into a prompt. Keeping the
view explicit is what makes isolation testable: a test can assert that the
examiner's prompt never contains tutor notes because the ExaminerView has no
field for them.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from . import prompts
from .base import Agent
from .schemas import (
    AdvisorReply,
    ExaminerTurn,
    GraderVerdict,
    LectureReview,
    PacingPlan,
    TutorReply,
    VerifierVerdict,
)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items) if items else "- (none)"


# --- views -----------------------------------------------------------------


class TutorView(BaseModel):
    module_title: str
    module_summary: str
    outcomes: list[str]
    texts: list[str]
    problem_statement: str | None = None  # never the reference solution
    prior_attempts: list[str] = Field(default_factory=list)
    tutor_edge_notes: list[str] = Field(default_factory=list)

    def render(self) -> str:
        parts = [
            f"MODULE: {self.module_title}\n{self.module_summary}",
            "OUTCOMES:\n" + _bullets(self.outcomes),
            "TEXTS:\n" + _bullets(self.texts),
        ]
        if self.problem_statement:
            parts.append("PROBLEM UNDER DISCUSSION (not yet submitted; do not solve it):\n" + self.problem_statement)
        if self.prior_attempts:
            parts.append("LEARNER'S PRIOR ATTEMPTS:\n" + _bullets(self.prior_attempts))
        parts.append("YOUR EARLIER NOTES ON WHERE THIS LEARNER BROKE:\n" + _bullets(self.tutor_edge_notes))
        return "\n\n".join(parts)


class ExaminerView(BaseModel):
    exam_title: str
    standard: str
    modules: list[str]
    concepts: list[str]
    past_exam_record: list[str] = Field(default_factory=list)  # only exam outcomes, never tutor notes
    max_questions: int = 12

    def render(self) -> str:
        return "\n\n".join(
            [
                f"EXAM: {self.exam_title}\nPASS STANDARD: {self.standard}\nMAX QUESTIONS: {self.max_questions}",
                "MODULES IN SCOPE:\n" + _bullets(self.modules),
                "CONCEPTS IN SCOPE:\n" + _bullets(self.concepts),
                "LEARNER'S PAST EXAM RECORD (outcomes only):\n" + _bullets(self.past_exam_record),
            ]
        )


class GraderView(BaseModel):
    problem_statement: str
    reference_solution: str
    rubric: list[dict]
    submission: str
    deterministic_result: str | None = None

    def render(self) -> str:
        rub = _bullets([f"[{r['points']} pts] {r['criterion']}" for r in self.rubric])
        parts = [
            "PROBLEM:\n" + self.problem_statement,
            "REFERENCE SOLUTION:\n" + self.reference_solution,
            "RUBRIC:\n" + rub,
        ]
        if self.deterministic_result:
            parts.append("DETERMINISTIC CHECK (binding on final-answer points):\n" + self.deterministic_result)
        parts.append("SUBMISSION:\n" + self.submission)
        return "\n\n".join(parts)


class VerifierView(BaseModel):
    role_under_review: str
    inputs: str
    output: str
    reference_solution: str | None = None

    def render(self) -> str:
        parts = [f"AGENT UNDER REVIEW: {self.role_under_review}", "ITS INPUTS:\n" + self.inputs]
        if self.reference_solution:
            parts.append("REFERENCE SOLUTION (the agent under review may not have had this):\n" + self.reference_solution)
        parts.append("ITS OUTPUT:\n" + self.output)
        return "\n\n".join(parts)


class PacerView(BaseModel):
    phase: str
    modules_in_phase: list[str]
    daily_blocks: list[str]
    days_idle: int
    retention: float
    due_cards: int
    recent_grades: list[str]
    exam_record: list[str]
    edge_notes: list[str]
    gate_status: str

    def render(self) -> str:
        return "\n\n".join(
            [
                f"PHASE: {self.phase}\nGATE: {self.gate_status}",
                "MODULES:\n" + _bullets(self.modules_in_phase),
                "DAILY BLOCK STRUCTURE:\n" + _bullets(self.daily_blocks),
                f"DAYS IDLE: {self.days_idle}\nRETENTION ESTIMATE: {self.retention:.2f}\nCARDS DUE: {self.due_cards}",
                "RECENT GRADES:\n" + _bullets(self.recent_grades),
                "EXAM RECORD:\n" + _bullets(self.exam_record),
                "EDGE NOTES (all sources):\n" + _bullets(self.edge_notes),
            ]
        )


class AdvisorView(BaseModel):
    anchors: list[str]
    weeks_remaining: float
    project_notes: list[str] = Field(default_factory=list)

    def render(self) -> str:
        return "\n\n".join(
            [
                "ANCHOR FIELDS:\n" + _bullets(self.anchors),
                f"WEEKS REMAINING IN PHASE: {self.weeks_remaining}",
                "PROJECT NOTES SO FAR:\n" + _bullets(self.project_notes),
            ]
        )


class CriticView(BaseModel):
    module_title: str
    outcomes: list[str]
    lecture: str

    def render(self) -> str:
        return "\n\n".join(
            [f"MODULE: {self.module_title}", "OUTCOMES THE LECTURE SHOULD COVER:\n" + _bullets(self.outcomes), "LECTURE:\n" + self.lecture]
        )


# --- agents ----------------------------------------------------------------


class Tutor(Agent):
    role = "tutor"
    system = prompts.TUTOR
    effort = "medium"

    def reply(self, view: TutorView, history: list[dict], message: str) -> TutorReply:
        msgs = [self.user("CONTEXT\n\n" + view.render()), self.assistant("Understood. I will teach within these bounds.")]
        msgs += history
        msgs.append(self.user(message))
        return self.ask(msgs, TutorReply)


class Examiner(Agent):
    role = "examiner"
    system = prompts.EXAMINER
    effort = "high"

    def open(self, view: ExaminerView) -> ExaminerTurn:
        return self.ask([self.user("CONTEXT\n\n" + view.render() + "\n\nBegin the examination with your first question.")], ExaminerTurn)

    def step(self, view: ExaminerView, history: list[dict], answer: str, asked: int) -> ExaminerTurn:
        msgs = [self.user("CONTEXT\n\n" + view.render())]
        msgs += history
        note = f"\n\n[Questions asked so far: {asked} of {view.max_questions}]"
        msgs.append(self.user(answer + note))
        return self.ask(msgs, ExaminerTurn)


class Grader(Agent):
    role = "grader"
    system = prompts.GRADER
    effort = "high"

    def grade(self, view: GraderView) -> GraderVerdict:
        return self.ask([self.user(view.render())], GraderVerdict)


class Verifier(Agent):
    role = "verifier"
    system = prompts.VERIFIER
    effort = "high"

    def check(self, view: VerifierView) -> VerifierVerdict:
        return self.ask([self.user(view.render())], VerifierVerdict)


class Pacer(Agent):
    role = "pacer"
    system = prompts.PACER
    effort = "medium"

    def plan(self, view: PacerView) -> PacingPlan:
        return self.ask([self.user(view.render() + "\n\nProduce today's plan.")], PacingPlan)


class Advisor(Agent):
    role = "advisor"
    system = prompts.ADVISOR
    effort = "high"

    def reply(self, view: AdvisorView, history: list[dict], message: str) -> AdvisorReply:
        msgs = [self.user("CONTEXT\n\n" + view.render()), self.assistant("Understood.")]
        msgs += history
        msgs.append(self.user(message))
        return self.ask(msgs, AdvisorReply)


class Critic(Agent):
    role = "critic"
    system = prompts.CRITIC
    effort = "high"

    def review(self, view: CriticView) -> LectureReview:
        return self.ask([self.user(view.render())], LectureReview)
