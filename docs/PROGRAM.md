# The Minimum: program design

## Goal and honest framing

Take a learner from a beginner start to the standard of a PhD qualifying exam in two anchor
fields and first-year graduate level in six others, across physics, chemistry, computer
science, electrical engineering, mechanical engineering, biomedical engineering, biology, and
mathematics, with no human faculty, in about 18 months at 26 hours a week.

Two things are said to every entrant on day one. First, this is a mastery program, not a
schedule: you stay in a phase until you pass its gate, however long that takes. Second, the
faculty is designed to be unimpressed. An undergraduate-level answer is a fail at every gate.

What the finisher is: at the entry gate of research in two fields, at first-year graduate
level in the other six, and demonstrably able to learn a new field to graduate level in two
months. That last property is the real deliverable. What the finisher is not: an SME in eight
fields. Nobody is.

## Design principles

- **Teach the shared core once.** Six modes of reasoning cover all eight fields: continuous
  dynamics, energy and thermodynamics, statistical inference, computation, feedback and
  control, information and evolution. ECE, ME, and BME share most of their mathematics, and
  CS and math are tools used by the rest.
- **Graduate texts from day one.** Every subject starts from the graduate standard and
  backfills prerequisites just in time. Brutal in the first phase, and the only way to reach
  the target.
- **Problems before lectures.** Each unit opens with problems the learner cannot yet solve.
  Reading produces recognition, not expertise.
- **Everything cumulative.** Any exam can draw on anything since day one. Forgetting is
  failure, and the retrieval system is the backbone of the program at this pace.
- **Teaching as assessment.** A weekly lecture on earlier material, graded for correctness
  first.
- **Measure first, model second.** A lab kit under about 300 dollars gives every physical
  science module a real experiment.
- **AI as a weapon, with verification.** Learners use AI tools for literature, derivations,
  and code. Every AI output used in submitted work must be independently verified and the
  verification shown. Unverified use is a failed submission.

## The four phases

1. **The mathematical and computational minimum, about 18 weeks.** Real analysis, linear
   algebra, ODEs and dynamical systems, PDEs and Fourier analysis, probability and inference,
   complex analysis, and computation in Python and C with numerical methods treated as part of
   the mathematics. Gate 1: a written exam at the level of a first-year graduate mathematics
   final, and a 30-minute oral.
2. **The physical sciences at graduate level, about 24 weeks.** Classical mechanics,
   electrodynamics, quantum mechanics, statistical mechanics, physical and quantum chemistry,
   algorithms and complexity, computer systems. Gate 2: a full written qualifying exam in
   physics at the pass standard of a strong department, and an oral. Anchor fields are chosen
   on passing.
3. **Engineered and living systems, about 18 weeks.** Signals, systems, and control taught once
   across ECE, ME, and BME; electromagnetics and devices; continuum mechanics and fluids; heat
   and mass transfer; molecular and cell biology as physics; physiology, biomechanics, and
   bioinstrumentation. A weekly reproduction of a published result with a documented
   limitation the authors did not state. Gate 3: a graduate final in every non-anchor field in
   one week, and an oral.
4. **Research and defense, about 18 weeks.** A reproduction-with-extension project in an anchor
   field under the advisor, written qualifying exams in both anchors, and a three-hour oral
   defense.

Phase lengths are expected durations, not deadlines.

## The day

About four hours on a standard day, scaled by the pacing agent to the learner's week:
retrieval first, always; new material from the graduate text; problem sets, with everything
submitted including failures; project or experiment work; and half an hour reading something
slightly too hard. Seven to eight hours of sleep is part of the program because consolidation
happens during sleep.

## Selection week

Before enrolment: a two-hour written exam on algebra, basic calculus, logic, programming, and
reading a technical paragraph for its claim and weakest point; then five observed days of the
real schedule. Selection is for learning speed and honesty about what one does not know, not
for prior knowledge. The engine ships this exam as `courses/science/exams/selection.yaml`.

## The faculty

No single agent plays every role, because an agent that tutors you is too lenient when it
examines you.

- **Tutor**, one per field, Socratic, never gives the solution to an unsubmitted problem.
- **Examiner**, isolated from the tutor's notes so it cannot steer around weak spots, probes
  until it finds the edge and records it.
- **Grader**, bound by the deterministic checks on final answers, grades the argument against
  a rubric and cites the text that earns or loses each point.
- **Verifier**, adversarial, checks every substantive output before the learner sees it.
- **Pacer**, owns the learner model, schedules retrieval, flags drift.
- **Advisor**, scopes and critiques the research project, acts as a hostile reviewer.
- **Critic**, grades the weekly lecture.

## What AI faculty cannot do, and the mitigations

- **Confident errors.** The verifier, tool-checked grading, and a pre-verified reference
  solution for every graded problem. Unverified grades are marked provisional.
- **Leniency.** Examiner isolation, rubric citation on every point, real past exams with
  published pass marks. The learner is told the examiner is designed to be unimpressed.
- **Gaming the tutor.** The solution lock is enforced by the orchestrator, not by the prompt.
- **Physical labs.** The kit and protocols; the learner uploads data, the grader checks
  plausibility against the physics.
- **Research taste.** Reproduction-with-extension projects, where the question is already
  known to be worth asking.
- **External validation.** Real past qualifying exams, external exams the learner can sit on
  their own, and submission of the final paper. The program can enforce a standard; it cannot
  confer recognition. The finisher proves it in the world.

## Assessment

Written gates are drawn fresh from the bank each attempt, with a cooldown between attempts.
Oral gates are run by the examiner to a named standard and end with a verdict, the level
reached, and the edge of understanding in plain language. Completion is by defense.

## Languages

Kept separate. They share nothing with the dependency graph, compete for the same hours with
no overlap, and need audio and a conversational examiner. The engine is course-agnostic so a
language course can run on it later as a second course.

## What is not yet built

A web front end; a bank large enough that gates stay fresh over many attempts (hundreds of
problems per phase); audio for oral exams; and a run against a live model, which the build
environment could not do. The engine is built so each of these is an addition, not a rewrite.
