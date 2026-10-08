# The Minimum

A self-contained, mastery-gated program that takes a learner from a beginner start to the
standard of a PhD qualifying exam in two anchor fields and first-year graduate level in six
others: physics, chemistry, computer science, electrical engineering, mechanical engineering,
biomedical engineering, biology, and mathematics. There is no human faculty. The faculty is a
set of AI agents designed to be adversarial toward each other and unimpressed by the learner.

The name is borrowed from Landau's Theoretical Minimum, the exam sequence that took entrants
from nothing to research-ready and that only 43 people passed in 28 years.

`docs/PROGRAM.md` is the program design. This file is about the software.

## What is here

```
minimum/              the engine (course-agnostic)
  models.py           course tree, problems, answers, learner records
  content.py          load and validate a course directory
  store.py            the learner model (SQLite)
  retrieval.py        spaced retrieval scheduler
  gates.py            mastery gates and exam drawing
  tools/              deterministic checkers: CAS, numeric, code sandbox
  llm.py              model client (Anthropic SDK) and a scripted fake
  agents/             the seven faculty roles, their views, prompts, schemas
  orchestrator.py     wiring and the three rules no prompt can enforce
  cli.py              command-line front end
  web/                browser front end (FastAPI, server-rendered)
  smoke.py            one real model call to verify a deployment
  courses/science/    the first course built on the engine (shipped as package data)
    course.yaml       phases, modules, texts, concepts, gates, exams, rules
    problems/*.yaml   the problem bank with reference solutions and rubrics
    exams/selection.yaml  the fixed selection-week exam
    labs/kit.yaml     the lab kit and protocols
tests/                engine tests and content self-checks
```

## Quickest path to working

```
pip install -e ".[web,dev]"
export ANTHROPIC_API_KEY=sk-ant-...      # or: ant auth login
minimum smoke                            # one real call: credentials, model, structured output
MINIMUM_TOKEN=choose-a-secret minimum-web
```

Open http://localhost:8000/?token=choose-a-secret. Enroll, start a module, submit a problem,
talk to the tutor. The web app and the CLI share the same engine and the same database.

## Deploy to Azure

`deploy/azure.sh` stands up Azure Container Apps with a persistent Azure Files share for the
learner database, builds the image from source, and stores the API key and access token as
secrets. It needs a logged-in `az` CLI and nothing else.

```
ANTHROPIC_API_KEY=sk-ant-... MINIMUM_TOKEN=choose-a-long-secret ./deploy/azure.sh
```

It prints the URL when done. Open `<url>/smoke` (after logging in with the token) to make one
real model call and confirm the faculty works. Re-running the script updates the app in place. The container also runs
anywhere else that takes a Dockerfile; set `MINIMUM_DB` to a path on a persistent volume and
`MINIMUM_TOKEN` to gate the browser.

The model is served by the first-party Anthropic API. To serve it from Claude on Microsoft
Foundry instead, swap the client construction in `minimum/llm.py` for the SDK's Foundry client;
every feature the engine uses is supported there.

## Command line

```
pip install -e ".[dev]"
python -m pytest
minimum validate
minimum enroll
minimum curriculum
minimum start p1.analysis
minimum problems p1.analysis
minimum show p1.analysis.003
minimum submit p1.analysis.003 my_answer.txt
minimum solution p1.analysis.003      # unlocks only after a submission
minimum tutor p1.analysis --problem p1.analysis.003
minimum review                        # spaced retrieval, first thing every day
minimum plan                          # today's plan from the pacing agent
minimum exam start gate1.written      # draws a fresh exam into a worksheet
minimum exam submit 1 exam-gate1.written-attempt1.md
minimum oral gate1.oral
minimum advance
```

The model is reached through the Anthropic SDK, which resolves credentials from the
environment or an `ant auth login` profile. The default model is set by `MINIMUM_MODEL`
and falls back to `claude-opus-5-5`. Pass `--offline` to run the engine with a scripted
faculty that does not teach and grades only what the deterministic checkers can decide.
Offline mode exists so gates, retrieval, exam drawing, and grading plumbing can be
exercised without a model; it says so in every reply.

## The rules the orchestrator enforces

Prompts shape behavior. These three rules are enforced in code because a prompt cannot
be trusted to enforce them.

1. **Solution lock.** A reference solution is never shown, to the learner or to the tutor,
   until a submission for that problem is on record. The tutor's view has no field for it.
2. **Examiner isolation.** The examiner sees exam outcomes only. The tutor's, grader's, and
   critic's notes on where the learner is weak never reach it. The pacing agent, which owns
   the whole learner model, is the only role that sees everything.
3. **Verification.** Every grade, tutor reply, and lecture review passes through the verifier
   before the learner sees it. A rejected output is re-run once with the verifier's issues
   attached, and the stored grade records whether verification succeeded. Deterministic
   checks (computer algebra, numeric tolerance, test suites) decide final-answer points and
   the grader cannot override them.

`tests/test_orchestrator.py` asserts all three by inspecting what each agent was shown.

## The faculty

| Role | Sees | Decides |
|---|---|---|
| Tutor | module, problem statement, learner's attempts, its own edge notes | what to teach next; never the solution |
| Examiner | exam scope, concepts, past exam outcomes | pass or fail in oral exams, and where the edge was |
| Grader | problem, reference solution, rubric, deterministic results, submission | rubric points for the argument |
| Verifier | the other agent's inputs and output, plus the reference | approve or list concrete errors |
| Pacer | the entire learner model | today's plan and the drift warning |
| Advisor | anchors, time remaining, project notes | scope and critique of the research project |
| Critic | module outcomes and the learner's lecture | correctness and clarity scores |

## Content

Problems are YAML. Each has a kind, a difficulty, a statement, an answer the engine can check,
a reference solution, and a rubric. Kinds:

- `symbolic`: checked with SymPy (simplification plus numeric sampling); learner ends with `ANSWER: <expr>`.
- `numeric`: checked to a tolerance.
- `code`: run in a subprocess against tests; the entry point must be defined.
- `proof`, `short`, `model`: graded against a rubric by the grader, checked by the verifier.

Symbolic, numeric, and code problems can also carry a `method_rubric` for the argument.
`tests/test_content.py` runs every reference solution through its own checker, so a wrong
reference cannot ship.

Exams are drawn from the bank by module and difficulty. Each attempt avoids problems the
learner has already seen in that exam, and attempts are separated by a cooldown.

## Adding a course

Create `minimum/courses/<id>/course.yaml` (or any directory, passed with `--course`) with the same shape, a `problems/` directory, and optionally
`exams/` and `labs/`. Run `minimum --course minimum/courses/<id> validate`. The engine, the agents, and
the CLI do not change. A language course would need audio in and out and a conversational
examiner, which is the one seam the engine does not yet have.

## Status

Built and tested: the engine, the faculty with isolation and verification, deterministic
grading, mastery gates, retrieval, the CLI, the web front end, the container, and a 77-problem
bank covering every module that feeds a gate. Not yet done: a run against a live model (this
build environment had no credentials; `minimum smoke` is the first thing to run), a larger
bank (a real gate needs hundreds of problems per phase so attempts stay fresh), audio for oral
exams, and multi-user accounts. The web app is single-learner behind one access token, and
tutor and oral-exam sessions live in process memory, so a restart ends an oral exam in
progress.
