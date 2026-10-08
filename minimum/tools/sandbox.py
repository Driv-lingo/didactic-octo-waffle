"""Run learner code in a subprocess with a timeout and no network.

This is a containment boundary against accidents, not a security boundary
against a hostile learner; the learner is running the program on their own
machine. Each test calls the entry point and compares the repr-literal result.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

from ..models import CodeTest

_HARNESS = r"""
import json, sys, ast, importlib.util, resource, socket
try:
    resource.setrlimit(resource.RLIMIT_AS, (1_000_000_000, 1_000_000_000))
except Exception:
    pass
socket.socket = None  # crude: no network
spec = importlib.util.spec_from_file_location("submission", sys.argv[1])
mod = importlib.util.module_from_spec(spec)
try:
    spec.loader.exec_module(mod)
except Exception as exc:
    print(json.dumps({"import_error": repr(exc)}))
    sys.exit(0)
tests = json.loads(sys.argv[2])
entry = sys.argv[3]
if not hasattr(mod, entry):
    print(json.dumps({"missing_entry": entry}))
    sys.exit(0)
results = []
ns = dict(vars(mod))
for t in tests:
    try:
        got = eval(t["call"], ns)
        exp = ast.literal_eval(t["expected"])
        results.append({"call": t["call"], "passed": got == exp, "got": repr(got), "expected": t["expected"]})
    except Exception as exc:
        results.append({"call": t["call"], "passed": False, "got": repr(exc), "expected": t["expected"]})
print(json.dumps({"results": results}))
"""


@dataclass
class CodeResult:
    passed: int
    total: int
    results: list[dict] = field(default_factory=list)
    error: str | None = None

    @property
    def all_passed(self) -> bool:
        return self.error is None and self.passed == self.total


def run_code_tests(source: str, entry_point: str, tests: list[CodeTest], timeout_s: float = 10.0) -> CodeResult:
    with tempfile.TemporaryDirectory() as td:
        sub = os.path.join(td, "submission.py")
        with open(sub, "w", encoding="utf-8") as fh:
            fh.write(source)
        harness = os.path.join(td, "harness.py")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write(_HARNESS)
        payload = json.dumps([t.model_dump() for t in tests])
        try:
            proc = subprocess.run(
                [sys.executable, "-I", harness, sub, payload, entry_point],
                capture_output=True,
                text=True,
                timeout=timeout_s,
                cwd=td,
                env={"PATH": os.environ.get("PATH", ""), "PYTHONHASHSEED": "0"},
            )
        except subprocess.TimeoutExpired:
            return CodeResult(0, len(tests), error=f"timed out after {timeout_s}s")
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return CodeResult(0, len(tests), error=f"harness produced no result; stderr: {proc.stderr[-2000:]}")
        if "import_error" in data:
            return CodeResult(0, len(tests), error=f"submission failed to import: {data['import_error']}")
        if "missing_entry" in data:
            return CodeResult(0, len(tests), error=f"entry point {entry_point} not defined")
        results = data["results"]
        return CodeResult(sum(1 for r in results if r["passed"]), len(tests), results=results)
