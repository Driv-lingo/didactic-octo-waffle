from minimum.agents.prompts import LECTURER


def test_lesson_generated_once_and_verified(orch, client, script):
    les = orch.get_lesson("p1.analysis", "uniform versus pointwise convergence")
    assert les["verified"] and "uniform versus pointwise" in les["body"]["title"]
    assert script.lessons_written == 1
    lect = [c for c in client.calls if c["role"] == "lecturer"]
    assert lect and lect[0]["system"] == LECTURER
    assert "CONCEPT TO TEACH: uniform versus pointwise convergence" in lect[0]["messages"][0]["content"]
    orch.get_lesson("p1.analysis", "uniform versus pointwise convergence")
    assert script.lessons_written == 1
    assert any(c["role"] == "verifier" and "Lesson on" in c["messages"][0]["content"] for c in client.calls)


def test_lesson_regenerated_when_verifier_rejects(orch, client, script):
    script.verifier_approves = False
    script.verifier_severity = "major"
    les = orch.get_lesson("p1.linalg", "singular value decomposition")
    assert script.lessons_written == 2
    assert les["verified"] is False and "scripted issue" in les["verifier_notes"]
    second = [c for c in client.calls if c["role"] == "lecturer"][1]
    assert "verifier found these problems" in second["messages"][-1]["content"]


def test_pregenerate_writes_every_concept(orch, script):
    t = orch.pregenerate_lessons("p1.ode")
    t.join(timeout=10)
    have = orch.store.lessons_for("p1.ode")
    assert set(have) == set(orch.bundle.course.module("p1.ode").concepts)


def test_check_answer_goes_to_tutor_with_outline(orch, client):
    orch.check_lesson_answer("tess", "p1.analysis", "mean value theorem and its proof", "because N depends on epsilon")
    call = [c for c in client.calls if c["role"] == "tutor"][-1]
    text = call["messages"][-1]["content"]
    assert "WHAT A CORRECT ANSWER MUST CONTAIN" in text and "LEARNER'S ANSWER" in text


def test_chat_and_oral_survive_a_new_orchestrator(orch, client, bundle):
    from minimum.orchestrator import Orchestrator

    orch.chat_turn("tess", "tutor:p1.ode:", "p1.ode", "hello")
    sess = orch.start_oral("tess", "gate1.oral")
    orch.oral_answer(sess, "first answer")
    orch2 = Orchestrator(bundle, orch.store, client)
    assert len(orch2.store.chat("tess", "tutor:p1.ode:")) == 2
    resumed = orch2.resume_oral("tess", "gate1.oral")
    assert resumed is not None and resumed.asked == 2 and resumed.history[1]["content"] == "first answer"
    while not resumed.done:
        orch2.oral_answer(resumed, "more")
    assert orch2.resume_oral("tess", "gate1.oral") is None
