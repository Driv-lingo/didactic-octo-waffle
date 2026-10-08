"""Command-line interface.

The CLI is deliberately thin: every command maps onto one orchestrator call,
so a web front end can replace it without touching the engine.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import click
import yaml

from .content import load_course
from .gates import GateError
from .llm import default_client
from .orchestrator import Orchestrator, SolutionLocked
from .store import Store

DEFAULT_COURSE = Path(__file__).resolve().parent / "courses" / "science"


class Ctx:
    def __init__(self, course: Path, db: Path, learner: str, offline: bool):
        self.course_path = course
        self.db_path = db
        self.learner = learner
        self.offline = offline
        self._orch: Orchestrator | None = None
        self.bundle = load_course(course)

    @property
    def orch(self) -> Orchestrator:
        if self._orch is None:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self._orch = Orchestrator(self.bundle, Store(self.db_path), default_client(self.offline))
        return self._orch


@click.group()
@click.option("--course", type=click.Path(path_type=Path), default=DEFAULT_COURSE, show_default=True, help="Course directory.")
@click.option("--db", type=click.Path(path_type=Path), default=Path(".minimum/learner.db"), show_default=True, help="Learner database.")
@click.option("--learner", default=lambda: os.environ.get("MINIMUM_LEARNER", "me"), help="Learner name (env MINIMUM_LEARNER).")
@click.option("--offline", is_flag=True, help="Use the scripted faculty; no model calls.")
@click.pass_context
def main(ctx: click.Context, course: Path, db: Path, learner: str, offline: bool) -> None:
    """minimum: a mastery-gated, AI-taught program engine."""
    ctx.obj = Ctx(course, db, learner, offline)


@main.command()
@click.pass_obj
def validate(c: Ctx) -> None:
    """Validate the course content."""
    errors = c.bundle.validate_bundle()
    course = c.bundle.course
    click.echo(f"{course.title}: {len(course.phases)} phases, {len(course.all_modules())} modules, {len(c.bundle.problems)} problems, {len(course.exams)} exams")
    if errors:
        for e in errors:
            click.echo(f"ERROR {e}")
        sys.exit(1)
    click.echo("content OK")


@main.command()
def smoke() -> None:
    """Make one real model call to verify credentials and structured outputs."""
    import anthropic

    from .smoke import run_smoke

    try:
        result = run_smoke()
    except anthropic.AuthenticationError:
        click.echo("No valid credentials. Set ANTHROPIC_API_KEY, or run `ant auth login`.")
        sys.exit(2)
    except anthropic.APIConnectionError as exc:
        click.echo(f"Could not reach the API: {exc}")
        sys.exit(2)
    click.echo(json.dumps(result, indent=2))
    if not result["answer_correct"]:
        click.echo("The model answered, but the answer was wrong. Check MINIMUM_MODEL.")
        sys.exit(1)


@main.command()
@click.pass_obj
def enroll(c: Ctx) -> None:
    """Enroll the learner in the course."""
    c.orch.enroll(c.learner)
    course = c.bundle.course
    click.echo(f"Enrolled {c.learner} in {course.title}.")
    click.echo("")
    for r in course.rules:
        click.echo(f"- {r}")


@main.command()
@click.pass_obj
def status(c: Ctx) -> None:
    """Where the learner stands."""
    click.echo(json.dumps(c.orch.status(c.learner), indent=2, default=str))


@main.command()
@click.pass_obj
def curriculum(c: Ctx) -> None:
    """Print the phases, modules, and gates."""
    for ph in c.bundle.course.phases:
        click.echo(f"\n{ph.id}  {ph.title}  ({ph.weeks} weeks)")
        for m in ph.modules:
            n = len(c.bundle.problems_for(m.id))
            click.echo(f"  {m.id:14s} {m.title:55s} {m.weeks:>4} wk  {n:>2} problems")
        g = c.bundle.course.gate_after(ph.id)
        if g:
            click.echo(f"  -> {g.id}: {', '.join(g.exams)}")


@main.command()
@click.argument("module_id")
@click.pass_obj
def start(c: Ctx, module_id: str) -> None:
    """Start a module: creates its retrieval cards and prints the reading."""
    m = c.bundle.course.module(module_id)
    n = c.orch.start_module(c.learner, module_id)
    click.echo(f"{m.title}\n{m.summary}\n")
    click.echo("Texts:")
    for t in m.texts:
        click.echo(f"  - {t.title} ({t.author}){' [open access: ' + t.url + ']' if t.open_access and t.url else ''}{': ' + t.note if t.note else ''}")
    if m.project:
        click.echo(f"\nProject: {m.project}")
    click.echo(f"\n{n} retrieval cards created.")


@main.command()
@click.pass_obj
def today(c: Ctx) -> None:
    """The guided day: ordered steps, with what is done."""
    from .day import load_or_build_day

    steps, done = load_or_build_day(c.bundle, c.orch.store, c.learner)
    for i, st in enumerate(steps, 1):
        mark = "✓" if st.id in done else " "
        click.echo(f"[{mark}] {i}. {st.title}  ({st.minutes} min)\n       {st.detail}")


@main.command()
@click.argument("module_id")
@click.argument("index", type=int)
@click.pass_obj
def lesson(c: Ctx, module_id: str, index: int) -> None:
    """Print a lesson (generated and verified on first request)."""
    m = c.bundle.course.module(module_id)
    les = c.orch.get_lesson(module_id, m.concepts[index])
    b = les["body"]
    click.echo(f"# {b['title']}\nIn the text: {b['textbook_section']}" + ("" if les["verified"] else "\n[UNVERIFIED: " + les["verifier_notes"] + "]"))
    click.echo("\n" + b["why_it_matters"] + "\n\n" + b["body_markdown"])
    for i, ex in enumerate(b["worked_examples"], 1):
        click.echo(f"\n## Example {i}\n{ex['problem']}\n\nSolution:\n{ex['solution']}")
    click.echo(f"\n## Mistake to watch for\n{b['common_mistake']}\n\n## Check question\n{b['check_question']}")


@main.command()
@click.option("--timeout", default=15.0, show_default=True)
@click.pass_obj
def links(c: Ctx, timeout: float) -> None:
    """Check every material and text URL in the course resolves."""
    import urllib.error
    import urllib.request

    bad = 0
    seen = set()
    for m in c.bundle.course.all_modules():
        items = [(x.title, x.url) for x in m.materials] + [(t.title, t.url) for t in m.texts if t.url]
        for title, url in items:
            if url in seen:
                continue
            seen.add(url)
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (minimum link check)"}, method="GET")
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    code = resp.status
            except urllib.error.HTTPError as exc:
                code = exc.code
            except Exception as exc:  # noqa: BLE001
                code = f"ERR {type(exc).__name__}"
            ok = isinstance(code, int) and code < 400
            bad += 0 if ok else 1
            click.echo(f"{'ok ' if ok else 'BAD'} {code}  {m.id:14s} {url}")
    click.echo(f"\n{len(seen)} links checked, {bad} bad")
    if bad:
        sys.exit(1)


@main.command()
@click.argument("module_id", required=False)
@click.pass_obj
def problems(c: Ctx, module_id: str | None) -> None:
    """List problems, optionally for one module."""
    ps = c.bundle.problems_for(module_id) if module_id else sorted(c.bundle.problems.values(), key=lambda p: p.id)
    for p in ps:
        done = "done" if c.orch.store.has_submitted(c.learner, p.id) else "    "
        click.echo(f"{done} {p.id:20s} d{p.difficulty} {p.kind.value:9s} {p.max_score:>3} pts  {p.statement.strip().splitlines()[0][:70]}")


@main.command()
@click.argument("problem_id")
@click.pass_obj
def show(c: Ctx, problem_id: str) -> None:
    """Print a problem statement."""
    p = c.orch.problem(problem_id)
    click.echo(f"{p.id}  (difficulty {p.difficulty}, {p.max_score} points, {p.kind.value})\n")
    click.echo(p.statement)
    if p.grading_rubric:
        click.echo("Graded on:")
        for r in p.grading_rubric:
            click.echo(f"  [{r.points}] {r.criterion}")


@main.command()
@click.argument("problem_id")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
def submit(c: Ctx, problem_id: str, file: Path) -> None:
    """Submit a solution file for grading."""
    g = c.orch.submit(c.learner, problem_id, file.read_text(encoding="utf-8"))
    click.echo(f"Score {g.score}/{g.max_score}" + ("" if g.verified else "  [UNVERIFIED: provisional]"))
    for b in g.rubric_breakdown:
        click.echo(f"  [{b['points_awarded']}/{b['points_possible']}] {b['criterion']}: {b['justification']}")
    click.echo("\n" + g.feedback)
    if g.verifier_notes:
        click.echo(f"\nVerifier notes: {g.verifier_notes}")


@main.command()
@click.argument("problem_id")
@click.pass_obj
def solution(c: Ctx, problem_id: str) -> None:
    """Show the reference solution (unlocks after submission)."""
    try:
        click.echo(c.orch.reveal_solution(c.learner, problem_id))
    except SolutionLocked as exc:
        click.echo(f"LOCKED: {exc}")
        sys.exit(2)


@main.command()
@click.argument("module_id")
@click.option("--problem", "problem_id", default=None, help="Problem to discuss (its solution stays locked).")
@click.pass_obj
def tutor(c: Ctx, module_id: str, problem_id: str | None) -> None:
    """Talk to the module's tutor. Type 'quit' to leave."""
    history: list[dict] = []
    click.echo("Tutor ready. Ask.")
    while True:
        msg = click.prompt("you", prompt_suffix="> ")
        if msg.strip().lower() in {"quit", "exit"}:
            break
        reply, ok = c.orch.tutor_turn(c.learner, module_id, history, msg, problem_id)
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": reply.reply})
        click.echo(f"\ntutor> {reply.reply}" + ("" if ok else "  [unverified]") + "\n")


@main.group()
def exam() -> None:
    """Written exams."""


@exam.command("start")
@click.argument("exam_id")
@click.option("--out", type=click.Path(path_type=Path), default=None, help="Worksheet path.")
@click.pass_obj
def exam_start(c: Ctx, exam_id: str, out: Path | None) -> None:
    """Draw an exam and write a worksheet to fill in."""
    try:
        att_id, ps = c.orch.start_written(c.learner, exam_id)
    except GateError as exc:
        click.echo(str(exc))
        sys.exit(2)
    out = out or Path(f"exam-{exam_id}-attempt{att_id}.md")
    spec = c.bundle.fixed_exams.get(exam_id)
    minutes = spec.duration_minutes if spec else c.bundle.course.exam(exam_id).duration_minutes
    lines = [f"# {exam_id} attempt {att_id}", f"Time allowed: {minutes} minutes. Closed book.", ""]
    for p in ps:
        lines += [f"## {p.id}  ({p.max_score} points)", "", p.statement.strip(), "", "```answer", "", "```", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    click.echo(f"Attempt {att_id}: {len(ps)} problems written to {out}. Fill each ```answer block, then: minimum exam submit {att_id} {out}")


@exam.command("submit")
@click.argument("attempt_id", type=int)
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
def exam_submit(c: Ctx, attempt_id: int, file: Path) -> None:
    """Grade a filled-in worksheet."""
    answers = _parse_worksheet(file.read_text(encoding="utf-8"))
    try:
        att = c.orch.submit_written(c.learner, attempt_id, answers)
    except GateError as exc:
        click.echo(str(exc))
        sys.exit(2)
    click.echo(f"{att.exam_id}: {att.score}/{att.max_score}  {'PASS' if att.passed else 'FAIL'}")


def _parse_worksheet(text: str) -> dict[str, str]:
    answers: dict[str, str] = {}
    current: str | None = None
    buf: list[str] | None = None
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].split()[0]
        elif line.strip() == "```answer":
            buf = []
        elif line.strip() == "```" and buf is not None:
            if current:
                answers[current] = "\n".join(buf).strip()
            buf = None
        elif buf is not None:
            buf.append(line)
    return answers


@main.command()
@click.argument("exam_id")
@click.pass_obj
def oral(c: Ctx, exam_id: str) -> None:
    """Sit an oral exam with the examiner."""
    try:
        sess = c.orch.start_oral(c.learner, exam_id)
    except GateError as exc:
        click.echo(str(exc))
        sys.exit(2)
    click.echo(f"examiner> {sess.last.question}\n")
    while not sess.done:
        ans = click.prompt("you", prompt_suffix="> ")
        turn = c.orch.oral_answer(sess, ans)
        if turn.done:
            click.echo(f"\nVERDICT: {turn.verdict}\nLevel reached: {turn.level_reached}\n{turn.assessment}")
        else:
            click.echo(f"\nexaminer> {turn.question}\n")


@main.command()
@click.pass_obj
def advance(c: Ctx) -> None:
    """Advance to the next phase if the gate is passed."""
    ok, msg = c.orch.advance(c.learner)
    click.echo(("Advanced to " if ok else "Not advanced: ") + msg)


@main.command()
@click.pass_obj
def review(c: Ctx) -> None:
    """Spaced retrieval over due cards. Grade yourself 0-5 honestly."""
    due = c.orch.due(c.learner)
    if not due:
        click.echo("Nothing due.")
        return
    click.echo(f"{len(due)} cards due. 0-2 forgot, 3 hard, 4 good, 5 easy.\n")
    for card in due:
        click.echo(card["prompt"])
        click.prompt("(answer out loud, then press enter)", default="", show_default=False)
        g = click.prompt("grade", type=click.IntRange(0, 5))
        updated = c.orch.review_card(c.learner, card["card_id"], g)
        click.echo(f"  next in {updated['interval_days']:.1f} days\n")


@main.command()
@click.pass_obj
def plan(c: Ctx) -> None:
    """Today's plan from the pacing agent."""
    p = c.orch.plan(c.learner)
    click.echo(p.message + "\n")
    for b in p.blocks:
        click.echo(f"{b.minutes:>4} min  {b.block:18s} {b.task}\n          why: {b.why}")
    if p.warnings:
        click.echo("\nWarnings:")
        for w in p.warnings:
            click.echo(f"  - {w}")
    click.echo(f"\nDrift risk: {p.drift_risk}")


@main.command()
@click.argument("module_id")
@click.argument("file", type=click.Path(exists=True, path_type=Path))
@click.pass_obj
def lecture(c: Ctx, module_id: str, file: Path) -> None:
    """Submit this week's lecture for review."""
    rev, ok = c.orch.critique(c.learner, module_id, file.read_text(encoding="utf-8"))
    click.echo(f"Correctness {rev.correctness}/10  Clarity {rev.clarity}/10" + ("" if ok else "  [unverified]"))
    for e in rev.errors:
        click.echo(f"  error: {e}")
    click.echo("\n" + rev.feedback)


@main.command()
@click.pass_obj
def advise(c: Ctx) -> None:
    """Talk to the research advisor. Type 'quit' to leave."""
    history: list[dict] = []
    while True:
        msg = click.prompt("you", prompt_suffix="> ")
        if msg.strip().lower() in {"quit", "exit"}:
            break
        r = c.orch.advise(c.learner, history, msg)
        history.append({"role": "user", "content": msg})
        history.append({"role": "assistant", "content": r.reply})
        click.echo(f"\nadvisor> {r.reply}")
        for x in r.critique:
            click.echo(f"  critique: {x}")
        for x in r.next_actions:
            click.echo(f"  next: {x}")
        click.echo("")


@main.command()
@click.argument("fields", nargs=2)
@click.pass_obj
def anchors(c: Ctx, fields: tuple[str, str]) -> None:
    """Choose the two anchor fields (after gate 2)."""
    valid = {m.field for m in c.bundle.course.all_modules()}
    for f in fields:
        if f not in valid:
            click.echo(f"unknown field {f}; choose from {sorted(valid)}")
            sys.exit(2)
    c.orch.store.set_anchors(c.learner, list(fields))
    click.echo(f"Anchors set: {fields[0]}, {fields[1]}")


@main.command()
@click.pass_obj
def kit(c: Ctx) -> None:
    """Print the lab kit and protocols."""
    k = c.bundle.lab_kit
    if not k:
        click.echo("This course has no lab kit.")
        return
    click.echo(f"Budget: about ${k.budget_usd}\n")
    for it in k.items:
        click.echo(f"  ${it['approx_usd']:>3}  {it['item']}")
    click.echo("\nProtocols:")
    for p in k.protocols:
        click.echo(f"\n  [{p['module']}] {p['title']}\n    measure: {p['measure']}\n    model:   {p['model']}\n    report:  {p['report']}")


if __name__ == "__main__":
    main()
