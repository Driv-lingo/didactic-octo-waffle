"""Every reference answer in the bank must pass its own checker."""

import pytest

from minimum.models import CodeAnswer, NumericAnswer, SymbolicAnswer
from minimum.orchestrator import _final_answer
from minimum.tools import check_numeric, check_symbolic, run_code_tests


def test_bundle_validates(bundle):
    assert bundle.validate_bundle() == []
    assert len(bundle.problems) >= 70


def _all_problems(bundle):
    ps = list(bundle.problems.values())
    for ex in bundle.fixed_exams.values():
        ps.extend(ex.problems)
    return ps


def test_symbolic_references_are_self_consistent(bundle):
    for p in _all_problems(bundle):
        if isinstance(p.answer, SymbolicAnswer):
            r = check_symbolic(p.answer.expr, p.answer.expr, p.answer.symbols)
            assert r.equivalent, f"{p.id}: reference does not parse: {r.detail}"
            final = _final_answer(p.reference_solution)
            r2 = check_symbolic(final, p.answer.expr, p.answer.symbols)
            assert r2.equivalent, f"{p.id}: reference solution's ANSWER line '{final}' disagrees with answer.expr: {r2.detail}"


def test_numeric_references_match_solution_text(bundle):
    for p in _all_problems(bundle):
        if isinstance(p.answer, NumericAnswer):
            final = _final_answer(p.reference_solution)
            r = check_numeric(final, p.answer.value, p.answer.rel_tol, p.answer.abs_tol)
            assert r.correct, f"{p.id}: {r.detail}"


def test_code_references_pass_their_tests(bundle):
    for p in _all_problems(bundle):
        if isinstance(p.answer, CodeAnswer):
            r = run_code_tests(p.reference_solution, p.answer.entry_point, p.answer.tests, p.answer.timeout_s)
            assert r.all_passed, f"{p.id}: {r.error} {r.results}"


def test_every_module_has_concepts_and_texts(bundle):
    for m in bundle.course.all_modules():
        assert m.concepts, m.id
        assert m.outcomes, m.id
        if not m.id.startswith("p4."):
            assert m.texts, m.id


def test_exam_pools_are_spread_across_modules(bundle):
    for e in bundle.course.exams:
        if e.oral:
            continue
        mods = {p.module for p in bundle.problems.values() if p.module in e.modules and p.difficulty >= e.min_difficulty and p.gate_eligible}
        assert len(mods) >= min(3, len(e.modules)), e.id


def test_rubric_points_positive(bundle):
    for p in _all_problems(bundle):
        for r in p.grading_rubric:
            assert r.points > 0, p.id
        assert p.max_score > 0, p.id


@pytest.mark.parametrize("gate_id", ["gate1", "gate2", "gate3", "final"])
def test_gates_reference_existing_exams(bundle, gate_id):
    g = bundle.course.gate(gate_id)
    for e in g.exams:
        bundle.course.exam(e)
