"""Scripted faculty for runs without a model.

Offline mode exists so the engine (gates, retrieval, deterministic grading,
exam drawing) can be exercised end to end. It does not teach. Every reply
says so, and prose grading awards nothing, because a grader that cannot read
must not award points.
"""

from __future__ import annotations

from pydantic import BaseModel

from .schemas import (
    AdvisorReply,
    ExaminerTurn,
    GraderVerdict,
    LectureReview,
    LessonOut,
    PacingPlan,
    PlanBlock,
    RubricScore,
    TutorReply,
    VerifierVerdict,
    WorkedExample,
)

OFFLINE = "[offline faculty: no model is connected; this is a placeholder]"


def _last_user_text(messages: list[dict]) -> str:
    for m in reversed(messages):
        if m["role"] == "user":
            return m["content"] if isinstance(m["content"], str) else ""
    return ""


def _rubric_from_prompt(text: str) -> list[tuple[int, str]]:
    out = []
    for line in text.splitlines():
        line = line.strip()
        if line.startswith("- [") and " pts] " in line:
            pts = int(line[3 : line.index(" pts]")])
            out.append((pts, line.split(" pts] ", 1)[1]))
    return out


def offline_handler(role: str, system: str, messages: list[dict], schema: type[BaseModel]) -> BaseModel:
    text = _last_user_text(messages)
    if schema is TutorReply:
        return TutorReply(
            reply=OFFLINE + " Solutions unlock after submission. What is the first quantity you would define?",
            edge_notes=[],
            revealed_solution=False,
        )
    if schema is GraderVerdict:
        rubric = _rubric_from_prompt(text)
        return GraderVerdict(
            rubric_scores=[
                RubricScore(criterion=c, points_possible=p, points_awarded=0, justification=OFFLINE + " prose cannot be graded offline")
                for p, c in rubric
            ],
            feedback=OFFLINE + " Only deterministic checks were applied.",
            edge_notes=[],
        )
    if schema is VerifierVerdict:
        return VerifierVerdict(approved=False, issues=[OFFLINE + " nothing was verified"], severity="major")
    if schema is ExaminerTurn:
        asked = 0
        if "[Questions asked so far:" in text:
            asked = int(text.split("[Questions asked so far:")[1].split(" of")[0])
        if asked >= 2:
            return ExaminerTurn(
                question="",
                probing="",
                difficulty=1,
                done=True,
                verdict="fail",
                level_reached="not assessed",
                assessment=OFFLINE + " An offline examiner cannot assess; recorded as a fail.",
            )
        return ExaminerTurn(
            question=OFFLINE + " State the definition you would start from.",
            probing="definitions",
            difficulty=1,
            done=False,
        )
    if schema is PacingPlan:
        due = 0
        for line in text.splitlines():
            if line.startswith("CARDS DUE:"):
                due = int(line.split(":")[1])
        blocks = [PlanBlock(block="retrieval", minutes=max(15, 2 * due), task=f"Review {due} due cards", why="retrieval comes first")]
        blocks.append(PlanBlock(block="problem sets", minutes=120, task="Work the current module's problem set", why="default offline plan"))
        return PacingPlan(blocks=blocks, warnings=[OFFLINE], drift_risk="unknown", message=OFFLINE + " Default plan only.")
    if schema is LessonOut:
        return LessonOut(title=OFFLINE + " lesson placeholder", textbook_section="see the module's primary text", why_it_matters=OFFLINE,
                         body_markdown=OFFLINE + " No lesson can be written without a model. Read the primary text's section for this concept.",
                         worked_examples=[WorkedExample(problem=OFFLINE, solution=OFFLINE), WorkedExample(problem=OFFLINE, solution=OFFLINE)],
                         common_mistake=OFFLINE, check_question=OFFLINE, check_answer_outline=OFFLINE)
    if schema is AdvisorReply:
        return AdvisorReply(reply=OFFLINE, critique=[], next_actions=[])
    if schema is LectureReview:
        return LectureReview(correctness=0, clarity=0, errors=[OFFLINE], feedback=OFFLINE + " Not reviewed.")
    raise TypeError(f"offline handler has no script for {schema.__name__}")
