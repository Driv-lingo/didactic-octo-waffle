from __future__ import annotations

from pathlib import Path

import pytest

from minimum.agents.schemas import (
    AdvisorReply,
    ExaminerTurn,
    GraderVerdict,
    LectureReview,
    PacingPlan,
    PlanBlock,
    RubricScore,
    TutorReply,
    VerifierVerdict,
)
from minimum.content import load_course
from minimum.llm import FakeClient
from minimum.orchestrator import Orchestrator
from minimum.store import Store

ROOT = Path(__file__).resolve().parent.parent
COURSE = ROOT / "minimum" / "courses" / "science"


@pytest.fixture(scope="session")
def bundle():
    return load_course(COURSE)


def _rubric(text: str) -> list[tuple[int, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- [") and " pts] " in line:
            out.append((int(line[3 : line.index(" pts]")]), line.split(" pts] ", 1)[1]))
    return out


class Script:
    """A configurable fake faculty. Tests mutate attributes to steer behavior."""

    def __init__(self):
        self.grader_fraction = 1.0  # fraction of rubric points to award
        self.verifier_approves = True
        self.verifier_severity = "none"
        self.tutor_reveals = False
        self.examiner_pass = True
        self.examiner_questions = 3
        self.tutor_edges = []

    def __call__(self, role, system, messages, schema):
        text = messages[-1]["content"] if isinstance(messages[-1]["content"], str) else ""
        if schema is TutorReply:
            return TutorReply(reply="What is the first definition you need?", edge_notes=list(self.tutor_edges), revealed_solution=self.tutor_reveals)
        if schema is GraderVerdict:
            prompt = "\n".join(m["content"] for m in messages if isinstance(m["content"], str))
            scores = [
                RubricScore(criterion=c, points_possible=p, points_awarded=int(round(p * self.grader_fraction)), justification="scripted")
                for p, c in _rubric(prompt)
            ]
            return GraderVerdict(rubric_scores=scores, feedback="scripted feedback", edge_notes=[])
        if schema is VerifierVerdict:
            return VerifierVerdict(approved=self.verifier_approves, issues=[] if self.verifier_approves else ["scripted issue"], severity=self.verifier_severity)
        if schema is ExaminerTurn:
            asked = 0
            if "[Questions asked so far:" in text:
                asked = int(text.split("[Questions asked so far:")[1].split(" of")[0])
            if asked >= self.examiner_questions:
                return ExaminerTurn(question="", probing="", difficulty=3, done=True, verdict="pass" if self.examiner_pass else "fail", level_reached="graduate", assessment="scripted")
            return ExaminerTurn(question=f"Question {asked + 1}?", probing="x", difficulty=3, done=False)
        if schema is PacingPlan:
            return PacingPlan(blocks=[PlanBlock(block="retrieval", minutes=30, task="review", why="first")], warnings=[], drift_risk="low", message="ok")
        if schema is AdvisorReply:
            return AdvisorReply(reply="narrow it", critique=["too broad"], next_actions=["pick a paper"])
        if schema is LectureReview:
            return LectureReview(correctness=8, clarity=7, errors=[], feedback="fine")
        raise TypeError(schema)


@pytest.fixture
def script():
    return Script()


@pytest.fixture
def client(script):
    return FakeClient(script)


@pytest.fixture
def orch(bundle, client):
    o = Orchestrator(bundle, Store(":memory:"), client)
    o.enroll("tess")
    return o
