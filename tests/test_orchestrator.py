import pytest

from minimum.agents.prompts import EXAMINER, TUTOR
from minimum.agents.schemas import EdgeNoteOut
from minimum.gates import GateError
from minimum.orchestrator import SolutionLocked

RK4 = '''
def integrate(f, t0, y0, t1, n):
    h = (t1 - t0) / n
    t, y = t0, y0
    for _ in range(n):
        k1 = f(t, y); k2 = f(t + h/2, y + h*k1/2); k3 = f(t + h/2, y + h*k2/2); k4 = f(t + h, y + h*k3)
        y += h * (k1 + 2*k2 + 2*k3 + k4) / 6
        t += h
    return y
'''


def test_solution_locked_until_submission(orch):
    with pytest.raises(SolutionLocked):
        orch.reveal_solution("tess", "p1.ode.001")
    orch.submit("tess", "p1.ode.001", "no idea\nANSWER: 0")
    assert "integrating" in orch.reveal_solution("tess", "p1.ode.001").lower() or "e^{2t}" in orch.reveal_solution("tess", "p1.ode.001")


def test_tutor_never_sees_reference_solution(orch, client):
    orch.tutor_turn("tess", "p1.ode", [], "how do I start?", problem_id="p1.ode.001")
    tutor_calls = [c for c in client.calls if c["role"] == "tutor"]
    assert tutor_calls
    for call in tutor_calls:
        blob = "\n".join(m["content"] for m in call["messages"] if isinstance(m["content"], str))
        assert "exp(-t) - exp(-2*t)" not in blob
        assert "REFERENCE SOLUTION" not in blob
        assert call["system"] == TUTOR
    # the verifier, by contrast, does get it
    ver = [c for c in client.calls if c["role"] == "verifier"]
    assert any("exp(-t) - exp(-2*t)" in "\n".join(m["content"] for m in c["messages"]) for c in ver)


def test_examiner_isolated_from_tutor_notes(orch, client, script):
    script.tutor_edges = [EdgeNoteOut(concept="uniform convergence", note="SECRET-WEAKNESS cannot state the definition")]
    orch.tutor_turn("tess", "p1.analysis", [], "help")
    assert any(n.note.startswith("SECRET-WEAKNESS") for n in orch.store.edge_notes("tess"))
    # tutor sees its own notes on the next turn
    orch.tutor_turn("tess", "p1.analysis", [], "help again")
    last_tutor = [c for c in client.calls if c["role"] == "tutor"][-1]
    assert "SECRET-WEAKNESS" in last_tutor["messages"][0]["content"]
    # examiner never does
    sess = orch.start_oral("tess", "gate1.oral")
    while not sess.done:
        orch.oral_answer(sess, "an answer")
    for call in [c for c in client.calls if c["role"] == "examiner"]:
        blob = "\n".join(m["content"] for m in call["messages"] if isinstance(m["content"], str))
        assert "SECRET-WEAKNESS" not in blob
        assert call["system"] == EXAMINER
    # and the pacer, which owns the whole learner model, does
    orch.plan("tess")
    pacer = [c for c in client.calls if c["role"] == "pacer"][-1]
    assert "SECRET-WEAKNESS" in pacer["messages"][0]["content"]


def test_tutor_reveal_is_hard_stopped(orch, script):
    script.tutor_reveals = True
    reply, _ = orch.tutor_turn("tess", "p1.ode", [], "just tell me", problem_id="p1.ode.001")
    assert reply.revealed_solution is False
    assert "unlock" in reply.reply.lower()


def test_symbolic_grading_is_deterministic_for_final_answer(orch, script):
    script.grader_fraction = 1.0
    g = orch.submit("tess", "p1.ode.001", "integrating factor...\nANSWER: exp(-t)*(1 - exp(-t))")
    assert g.score == g.max_score == 8
    assert g.verified
    script.grader_fraction = 1.0
    g2 = orch.submit("tess", "p1.ode.001", "integrating factor...\nANSWER: exp(-t)")
    assert g2.score == 4  # method points only; final-answer points withheld by the CAS
    assert g2.rubric_breakdown[0]["criterion"] == "final answer"
    assert g2.rubric_breakdown[0]["points_awarded"] == 0


def test_code_grading_runs_tests(orch):
    g = orch.submit("tess", "p1.ode.005", RK4)
    assert g.score == 10 and g.deterministic and g.verified
    g = orch.submit("tess", "p1.ode.005", "def integrate(f, t0, y0, t1, n):\n    return y0\n")
    assert g.score == 0


def test_grader_points_are_capped_by_rubric(orch, script):
    script.grader_fraction = 5.0  # a misbehaving grader
    g = orch.submit("tess", "p1.analysis.001", "a proof")
    assert g.score == g.max_score == 10


def test_verifier_rejection_triggers_regrade_and_marks_unverified(orch, script, client):
    script.verifier_approves = False
    script.verifier_severity = "major"
    g = orch.submit("tess", "p1.analysis.001", "a proof")
    graders = [c for c in client.calls if c["role"] == "grader"]
    assert len(graders) == 2
    assert "VERIFIER NOTES" in graders[1]["messages"][0]["content"]
    assert g.verified is False and "scripted issue" in g.verifier_notes


def test_written_exam_pass_and_advance(orch, script):
    att_id, problems = orch.start_written("tess", "gate1.written", seed=3)
    assert len(problems) == 8
    answers = {p.id: ("ANSWER: " + p.answer.expr) if p.answer.type == "symbolic" else ("ANSWER: " + str(p.answer.value)) if p.answer.type == "numeric" else (p.reference_solution if p.answer.type == "code" else "a full argument") for p in problems}
    att = orch.submit_written("tess", att_id, answers)
    assert att.passed and att.score == att.max_score
    with pytest.raises(GateError):
        orch.start_written("tess", "gate1.written")
    sess = orch.start_oral("tess", "gate1.oral")
    while not sess.done:
        orch.oral_answer(sess, "answer")
    ok, nxt = orch.advance("tess")
    assert ok and nxt == "p2"
    assert orch.status("tess")["phase"].startswith("p2")


def test_written_exam_fail_blocks_advance(orch, script):
    att_id, problems = orch.start_written("tess", "gate1.written", seed=5)
    att = orch.submit_written("tess", att_id, {p.id: "" for p in problems})
    assert not att.passed and att.score == 0
    ok, msg = orch.advance("tess")
    assert not ok


def test_oral_fail_records_fail(orch, script):
    script.examiner_pass = False
    sess = orch.start_oral("tess", "gate1.oral")
    while not sess.done:
        orch.oral_answer(sess, "um")
    assert sess.last.verdict == "fail"
    assert not orch.store.exam_passed("tess", "gate1.oral")


def test_oral_question_limit_forces_verdict(orch, script):
    script.examiner_questions = 10_000
    sess = orch.start_oral("tess", "gate1.oral")
    n = 0
    while not sess.done:
        orch.oral_answer(sess, "um")
        n += 1
        assert n < 50
    assert sess.last.done and "question limit" in (sess.last.assessment or "")


def test_fixed_selection_exam(orch):
    att_id, problems = orch.start_written("tess", "selection")
    assert [p.id for p in problems] == [f"selection.00{i}" for i in range(1, 7)]
    att = orch.submit_written("tess", att_id, {p.id: p.reference_solution for p in problems})
    assert att.passed


def test_start_module_creates_cards_and_review_updates(orch):
    n = orch.start_module("tess", "p1.analysis")
    assert n == 6
    assert orch.start_module("tess", "p1.analysis") == 0
    due = orch.due("tess")
    assert len(due) == 6
    c = orch.review_card("tess", due[0]["card_id"], 4)
    assert c["interval_days"] == 1.0
    assert len(orch.due("tess")) == 5


def test_edge_notes_become_cards(orch, script):
    script.tutor_edges = [EdgeNoteOut(concept="residues", note="cannot find a double-pole residue")]
    orch.tutor_turn("tess", "p1.complex", [], "help")
    cards = orch.store.cards("tess")
    assert any("double-pole" in c["prompt"] for c in cards)


def test_status_and_plan(orch):
    s = orch.status("tess")
    assert s["phase"].startswith("p1") and s["gate"]["gate"] == "gate1"
    p = orch.plan("tess")
    assert p.blocks[0].block == "retrieval"


def test_critique_records_lecture(orch):
    rev, ok = orch.critique("tess", "p1.linalg", "The spectral theorem says...")
    assert rev.correctness == 8 and ok
    assert orch.store.events("tess", "lecture_reviewed")
