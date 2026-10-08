"""Symbolic equivalence via SymPy.

The learner's final answer is parsed with a restricted transformation set and
compared with the reference by simplifying the difference and, as a backstop,
by numerical sampling. Any parse failure is a non-match, not an error, so a
malformed answer is simply wrong.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import sympy as sp
from sympy.parsing.sympy_parser import (
    convert_xor,
    implicit_multiplication_application,
    parse_expr,
    standard_transformations,
)

_TRANSFORMS = standard_transformations + (implicit_multiplication_application, convert_xor)


@dataclass
class SymbolicResult:
    equivalent: bool
    detail: str


def _parse(text: str, symbols: list[str]) -> sp.Expr:
    local = {s: sp.Symbol(s) for s in symbols}
    local.update({"e": sp.E, "pi": sp.pi, "I": sp.I, "oo": sp.oo})
    return parse_expr(text.strip(), local_dict=local, transformations=_TRANSFORMS, evaluate=True)


def check_symbolic(answer: str, reference: str, symbols: list[str], samples: int = 8) -> SymbolicResult:
    try:
        a = _parse(answer, symbols)
    except Exception as exc:  # noqa: BLE001 - any parse failure is a wrong answer
        return SymbolicResult(False, f"could not parse answer: {exc}")
    try:
        r = _parse(reference, symbols)
    except Exception as exc:  # noqa: BLE001
        return SymbolicResult(False, f"reference does not parse (content bug): {exc}")
    try:
        diff = sp.simplify(a - r)
        if diff == 0:
            return SymbolicResult(True, "simplify(answer - reference) == 0")
    except Exception as exc:  # noqa: BLE001
        diff = None
        detail = f"simplify failed: {exc}"
    else:
        detail = f"simplify(answer - reference) = {diff}"
    # Numerical backstop: sample the free symbols.
    free = sorted(a.free_symbols | r.free_symbols, key=lambda s: s.name)
    rng = random.Random(0)
    agree = 0
    tried = 0
    for _ in range(samples):
        subs = {s: rng.uniform(0.3, 2.7) for s in free}
        try:
            av = complex(a.evalf(subs=subs))
            rv = complex(r.evalf(subs=subs))
        except Exception:  # noqa: BLE001
            continue
        tried += 1
        if abs(av - rv) <= 1e-6 * max(1.0, abs(rv)):
            agree += 1
    if tried and agree == tried:
        return SymbolicResult(True, f"numerically equal at {tried} sample points; {detail}")
    return SymbolicResult(False, detail + f"; numeric agreement {agree}/{tried}")
