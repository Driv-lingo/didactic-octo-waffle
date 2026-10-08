"""Structured outputs for every faculty role."""

from __future__ import annotations

from pydantic import BaseModel, Field


class EdgeNoteOut(BaseModel):
    concept: str = Field(description="The specific concept where understanding broke")
    note: str = Field(description="What exactly the learner could not do, in one or two sentences")


class TutorReply(BaseModel):
    reply: str = Field(description="What to say to the learner")
    edge_notes: list[EdgeNoteOut] = Field(default_factory=list)
    revealed_solution: bool = Field(
        description="True if the reply gives away the solution to an unsubmitted problem. Must be false."
    )


class RubricScore(BaseModel):
    criterion: str
    points_possible: int
    points_awarded: int
    justification: str = Field(description="Cite the exact part of the submission that earns or loses the points")


class GraderVerdict(BaseModel):
    rubric_scores: list[RubricScore]
    feedback: str = Field(description="Direct feedback to the learner: what was wrong, what a correct argument needed")
    edge_notes: list[EdgeNoteOut] = Field(default_factory=list)


class VerifierVerdict(BaseModel):
    approved: bool = Field(description="True only if the output under review contains no factual, mathematical, or grading errors")
    issues: list[str] = Field(default_factory=list, description="Each concrete error found, with the correction")
    severity: str = Field(description="none, minor, or major")


class ExaminerTurn(BaseModel):
    question: str = Field(description="The next question to ask, or empty if the exam is over")
    probing: str = Field(description="The concept this question probes")
    difficulty: int = Field(ge=1, le=5)
    done: bool
    verdict: str | None = Field(default=None, description="When done: pass or fail")
    level_reached: str | None = Field(default=None, description="When done: the standard the learner demonstrated, e.g. 'first-year graduate'")
    assessment: str | None = Field(default=None, description="When done: where the edge of understanding was found")
    edge_notes: list[EdgeNoteOut] = Field(default_factory=list)


class PlanBlock(BaseModel):
    block: str
    minutes: int
    task: str
    why: str


class PacingPlan(BaseModel):
    blocks: list[PlanBlock]
    warnings: list[str] = Field(default_factory=list)
    drift_risk: str = Field(description="low, medium, or high, with the evidence in warnings")
    message: str = Field(description="Two sentences to the learner about where they stand")


class AdvisorReply(BaseModel):
    reply: str
    critique: list[str] = Field(default_factory=list, description="Specific weaknesses in the learner's current plan or draft")
    next_actions: list[str] = Field(default_factory=list)


class LectureReview(BaseModel):
    correctness: int = Field(ge=0, le=10)
    clarity: int = Field(ge=0, le=10)
    errors: list[str] = Field(default_factory=list, description="Every factual or mathematical error, quoted")
    feedback: str
    edge_notes: list[EdgeNoteOut] = Field(default_factory=list)
