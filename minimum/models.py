"""Data models shared by the engine, the agents, and the course content.

Everything here is course-agnostic. A course is a tree:
Course -> Phase -> Module -> (Problems, Texts, Concepts). Gates sit between
phases and are passed by exams drawn from the problem bank.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class ProblemKind(str, Enum):
    SYMBOLIC = "symbolic"  # closed-form expression, checked with a CAS
    NUMERIC = "numeric"  # a number with a tolerance
    CODE = "code"  # a program checked against tests
    PROOF = "proof"  # free-form argument, graded against a rubric
    SHORT = "short"  # short written answer, graded against a rubric
    MODEL = "model"  # build a model / estimate; graded against a rubric


class SymbolicAnswer(BaseModel):
    type: Literal["symbolic"] = "symbolic"
    expr: str
    symbols: list[str] = Field(default_factory=list)


class NumericAnswer(BaseModel):
    type: Literal["numeric"] = "numeric"
    value: float
    rel_tol: float = 1e-2
    abs_tol: float = 0.0
    unit: str | None = None


class CodeTest(BaseModel):
    """A single test for a code problem: run ``call`` and expect ``expected``."""

    call: str
    expected: str  # a Python literal, compared with ==


class CodeAnswer(BaseModel):
    type: Literal["code"] = "code"
    entry_point: str
    tests: list[CodeTest]
    timeout_s: float = 10.0


class RubricItem(BaseModel):
    points: int
    criterion: str


class RubricAnswer(BaseModel):
    type: Literal["rubric"] = "rubric"
    rubric: list[RubricItem]

    @property
    def total(self) -> int:
        return sum(i.points for i in self.rubric)


Answer = SymbolicAnswer | NumericAnswer | CodeAnswer | RubricAnswer


class Problem(BaseModel):
    id: str
    module: str
    kind: ProblemKind
    difficulty: int = Field(ge=1, le=5)
    statement: str
    answer: Answer = Field(discriminator="type")
    reference_solution: str
    concepts: list[str] = Field(default_factory=list)
    # For symbolic/numeric/code problems: points for the final answer, decided
    # deterministically. Rubric kinds ignore this and use answer.rubric.
    points: int = 10
    # Optional method points for deterministic kinds, graded by the grader.
    method_rubric: list[RubricItem] = Field(default_factory=list)
    # Problems tagged for gates are drawn into exams; others are practice.
    gate_eligible: bool = True

    @property
    def max_score(self) -> int:
        if isinstance(self.answer, RubricAnswer):
            return self.answer.total
        return self.points + sum(r.points for r in self.method_rubric)

    @property
    def grading_rubric(self) -> list[RubricItem]:
        if isinstance(self.answer, RubricAnswer):
            return self.answer.rubric
        return self.method_rubric

    @model_validator(mode="after")
    def _kind_matches_answer(self) -> "Problem":
        expected = {
            ProblemKind.SYMBOLIC: "symbolic",
            ProblemKind.NUMERIC: "numeric",
            ProblemKind.CODE: "code",
            ProblemKind.PROOF: "rubric",
            ProblemKind.SHORT: "rubric",
            ProblemKind.MODEL: "rubric",
        }[self.kind]
        if self.answer.type != expected:
            raise ValueError(
                f"problem {self.id}: kind {self.kind.value} needs a {expected} answer, got {self.answer.type}"
            )
        return self


class Text(BaseModel):
    title: str
    author: str
    note: str = ""
    open_access: bool = False
    url: str | None = None


class Module(BaseModel):
    id: str
    title: str
    mode: str  # which reasoning mode this module serves
    field: str  # physics, chemistry, cs, ece, me, bme, biology, math
    weeks: float
    summary: str
    outcomes: list[str]
    texts: list[Text] = Field(default_factory=list)
    concepts: list[str] = Field(default_factory=list)
    prerequisites: list[str] = Field(default_factory=list)
    project: str | None = None


class ExamSpec(BaseModel):
    """How an exam is drawn from the bank."""

    id: str
    title: str
    modules: list[str]
    n_problems: int
    min_difficulty: int = 1
    pass_mark: float = Field(ge=0, le=1)  # fraction of total points
    duration_minutes: int
    cooldown_days: int = 7  # between attempts
    oral: bool = False
    standard: str = ""  # for oral exams: the level the learner must demonstrate
    max_questions: int = 12


class Gate(BaseModel):
    id: str
    title: str
    after_phase: str
    exams: list[str]  # all must be passed
    description: str


class Phase(BaseModel):
    id: str
    title: str
    weeks: float
    goal: str
    modules: list[Module]


class DailyBlock(BaseModel):
    name: str
    hours: float
    description: str


class Course(BaseModel):
    id: str
    title: str
    tagline: str
    total_hours: int
    expected_months: int
    weekly_hours: int
    phases: list[Phase]
    gates: list[Gate]
    exams: list[ExamSpec]
    daily_blocks: list[DailyBlock]
    modes: dict[str, str]  # reasoning mode -> description
    rules: list[str]

    # --- lookups -------------------------------------------------------
    def module(self, module_id: str) -> Module:
        for ph in self.phases:
            for m in ph.modules:
                if m.id == module_id:
                    return m
        raise KeyError(module_id)

    def phase_of(self, module_id: str) -> Phase:
        for ph in self.phases:
            if any(m.id == module_id for m in ph.modules):
                return ph
        raise KeyError(module_id)

    def exam(self, exam_id: str) -> ExamSpec:
        for e in self.exams:
            if e.id == exam_id:
                return e
        raise KeyError(exam_id)

    def gate(self, gate_id: str) -> Gate:
        for g in self.gates:
            if g.id == gate_id:
                return g
        raise KeyError(gate_id)

    def gate_after(self, phase_id: str) -> Gate | None:
        for g in self.gates:
            if g.after_phase == phase_id:
                return g
        return None

    def all_modules(self) -> list[Module]:
        return [m for ph in self.phases for m in ph.modules]


# --- learner-side records -------------------------------------------------


class Submission(BaseModel):
    id: int | None = None
    learner: str
    problem_id: str
    content: str
    submitted_at: datetime
    exam_attempt_id: int | None = None


class Grade(BaseModel):
    submission_id: int
    score: float
    max_score: float
    deterministic: bool  # did a tool (CAS/tests) decide the answer points
    feedback: str
    rubric_breakdown: list[dict] = Field(default_factory=list)
    verified: bool = False
    verifier_notes: str = ""
    graded_at: datetime


class ExamAttempt(BaseModel):
    id: int | None = None
    learner: str
    exam_id: str
    problem_ids: list[str]
    started_at: datetime
    finished_at: datetime | None = None
    score: float | None = None
    max_score: float | None = None
    passed: bool | None = None


class EdgeNote(BaseModel):
    """Where understanding broke, as recorded by the tutor or examiner."""

    learner: str
    module: str
    concept: str
    note: str
    source: Literal["tutor", "examiner", "grader", "critic"]
    created_at: datetime
