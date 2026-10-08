"""One real model call to prove credentials, the model, and structured outputs work."""

from __future__ import annotations

import time

from pydantic import BaseModel

from .llm import DEFAULT_MODEL, AnthropicClient


class Probe(BaseModel):
    derivative: str
    one_line_reason: str


def run_smoke() -> dict:
    client = AnthropicClient()
    t0 = time.time()
    out = client.complete(
        role="smoke",
        system="You are a terse mathematics tutor. Answer in the requested structure only.",
        messages=[{"role": "user", "content": "Give the derivative of x**2 * sin(x) as a Python expression in x."}],
        schema=Probe,
        effort="low",
    )
    dt = time.time() - t0
    from .tools import check_symbolic

    ok = check_symbolic(out.derivative, "2*x*sin(x) + x**2*cos(x)", ["x"]).equivalent
    return {"model": DEFAULT_MODEL, "seconds": round(dt, 1), "structured_output": True, "answer": out.derivative, "answer_correct": ok}
