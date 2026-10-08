from minimum.models import CodeTest
from minimum.tools import check_numeric, check_symbolic, run_code_tests
from minimum.tools.numeric import extract_number


def test_symbolic_equivalent_forms():
    assert check_symbolic("-2*(-1)**n/n", "2*(-1)**(n+1)/n", ["n"]).equivalent
    assert check_symbolic("(exp(-t)*(1-exp(-t)))", "exp(-t) - exp(-2*t)", ["t"]).equivalent
    assert check_symbolic("x+1", "x + 1", ["x"]).equivalent
    assert check_symbolic("pi/2", "pi/2", []).equivalent
    assert check_symbolic("0.5", "1/2", []).equivalent


def test_symbolic_wrong_and_garbage():
    assert not check_symbolic("exp(-t)", "exp(-t) - exp(-2*t)", ["t"]).equivalent
    assert not check_symbolic("this is not math", "x", ["x"]).equivalent
    assert not check_symbolic("", "x", ["x"]).equivalent


def test_numeric_extraction_and_tolerance():
    assert extract_number("about 3.2e5 J") == 3.2e5
    assert extract_number("ANSWER: -89 mV") == -89
    assert extract_number("no digits") is None
    assert check_numeric("31.4", 31.4).correct
    assert check_numeric("32", 31.4, rel_tol=0.05).correct
    assert not check_numeric("40", 31.4, rel_tol=0.05).correct
    assert check_numeric("219", 219, abs_tol=0).correct


def test_sandbox_passes_and_counts():
    src = "def f(x):\n    return x * 2\n"
    r = run_code_tests(src, "f", [CodeTest(call="f(2)", expected="4"), CodeTest(call="f(3)", expected="7")])
    assert (r.passed, r.total) == (1, 2)
    assert r.results[1]["got"] == "6"


def test_sandbox_timeout_and_import_error():
    r = run_code_tests("while True: pass\n", "f", [CodeTest(call="f()", expected="1")], timeout_s=1.0)
    assert r.error and "timed out" in r.error
    r = run_code_tests("def f(:\n", "f", [CodeTest(call="f()", expected="1")])
    assert r.error and "import" in r.error


def test_sandbox_missing_entry_point():
    r = run_code_tests("def g():\n    return 1\n", "f", [CodeTest(call="g()", expected="1")])
    assert r.error and "entry point" in r.error
