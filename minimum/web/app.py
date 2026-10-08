from __future__ import annotations

import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Form, Request
import markdown as _markdown
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.middleware.base import BaseHTTPMiddleware

from ..content import load_course
from ..day import current_module, load_or_build_day, mark_done, next_concept, today_key
from ..gates import GateError, can_attempt
from ..llm import default_client
from ..orchestrator import Orchestrator, SolutionLocked
from ..persist import BlobSync, blob_sync_from_env
from ..store import Store

HERE = Path(__file__).resolve().parent
DEFAULT_COURSE = HERE.parent / "courses" / "science"


class TokenAuth(BaseHTTPMiddleware):
    """Single-user gate: a shared secret in a cookie or ?token=."""

    def __init__(self, app, token: str):
        super().__init__(app)
        self.token = token

    async def dispatch(self, request, call_next):
        if request.url.path == "/healthz":
            return await call_next(request)
        q = request.query_params.get("token")
        if q is not None:
            if q == self.token:
                target = request.url.path
                resp = RedirectResponse(target, status_code=303)
                resp.set_cookie("minimum_token", q, httponly=True, samesite="lax", max_age=60 * 60 * 24 * 365)
                return resp
            return HTMLResponse("<p>Wrong token.</p>", status_code=401)
        if request.cookies.get("minimum_token") == self.token:
            return await call_next(request)
        return HTMLResponse(
            '<form method="get"><p>Enter the access token.</p><input name="token" type="password" autofocus>'
            '<button>Enter</button></form>',
            status_code=401,
        )


def create_app(
    course: str | Path | None = None,
    db: str | Path | None = None,
    learner: str | None = None,
    offline: bool | None = None,
    token: str | None = None,
    blob_sync: BlobSync | None = None,
) -> FastAPI:
    course = Path(course or os.environ.get("MINIMUM_COURSE", DEFAULT_COURSE))
    db = Path(db or os.environ.get("MINIMUM_DB", "data/learner.db"))
    learner = learner or os.environ.get("MINIMUM_LEARNER", "me")
    if offline is None:
        offline = os.environ.get("MINIMUM_OFFLINE", "") == "1"
    token = token if token is not None else os.environ.get("MINIMUM_TOKEN", "")

    bundle = load_course(course)
    db.parent.mkdir(parents=True, exist_ok=True)
    sync = blob_sync if blob_sync is not None else blob_sync_from_env(db)
    restored = sync.restore() if sync is not None else False
    store = Store(db)
    if sync is not None:
        sync.attach(store)
    orch = Orchestrator(bundle, store, default_client(offline))

    @asynccontextmanager
    async def lifespan(_app):
        yield
        if sync is not None:
            sync.flush()

    app = FastAPI(title=bundle.course.title, lifespan=lifespan)
    if token:
        app.add_middleware(TokenAuth, token=token)
    templates = Jinja2Templates(directory=str(HERE / "templates"))
    templates.env.filters["md"] = lambda text: _markdown.markdown(text or "", extensions=["fenced_code", "tables"])
    templates.env.filters["num"] = lambda x: (str(int(x)) if float(x).is_integer() else f"{x:.1f}") if x is not None else ""

    def render(request: Request, name: str, **ctx):
        rec = orch.store.learner(learner)
        base = {"request": request, "course": bundle.course, "learner": learner, "enrolled": rec is not None,
                "offline": offline, "phase_id": rec["current_phase"] if rec else None,
                "ctx_phase": None, "ctx_module": None, "ctx_total": 0, "ctx_done": 0}
        if rec is not None:
            phase = next((p for p in bundle.course.phases if p.id == rec["current_phase"]), None)
            base["ctx_phase"] = f"{phase.id} · {phase.title}" if phase else None
            mid = rec.get("current_module")
            base["ctx_module"] = bundle.course.module(mid).title if mid else None
            saved = orch.store.day(learner, today_key())
            if saved:
                base["ctx_total"] = sum(st["minutes"] for st in saved["steps"])
                base["ctx_done"] = sum(st["minutes"] for st in saved["steps"] if st["id"] in set(saved["done"]))
        base.update(ctx)
        return templates.TemplateResponse(request, name, base)

    # --- health and enrolment ------------------------------------------
    @app.get("/healthz")
    def healthz():
        persistence = {"mode": "blob", "restored": restored, "uploads": sync.uploads, "last_error": sync.last_error} if sync is not None else {"mode": "local-only"}
        return {"ok": True, "course": bundle.course.id, "offline": offline, "build": os.environ.get("MINIMUM_BUILD", "dev"), "persistence": persistence}

    @app.get("/smoke")
    def smoke():
        """One real model call, behind the token. Proves credentials, model, and structured outputs."""
        if offline:
            return {"ok": False, "error": "offline faculty; no model is connected"}
        try:
            from ..smoke import run_smoke

            result = run_smoke()
        except Exception as exc:  # noqa: BLE001 - surface anything, this is a diagnostic
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        return {"ok": bool(result.get("answer_correct")), **result}

    @app.get("/", response_class=HTMLResponse)
    def today(request: Request):
        if orch.store.learner(learner) is None:
            return render(request, "enroll.html")
        steps, done = load_or_build_day(bundle, orch.store, learner)
        current = next((st for st in steps if st.id not in done), None)
        mid = current_module(bundle, orch.store, learner)
        mod = bundle.course.module(mid) if mid else None
        total = sum(st.minutes for st in steps)
        left = sum(st.minutes for st in steps if st.id not in done)
        return render(request, "today.html", steps=steps, done=done, current=current, mod=mod, total=total, left=left)

    @app.post("/today/done/{step_id:path}")
    def today_done(step_id: str):
        mark_done(orch.store, learner, step_id)
        return RedirectResponse("/", status_code=303)

    @app.post("/today/module")
    def today_module(module_id: str = Form(...)):
        bundle.course.module(module_id)
        orch.store.set_current_module(learner, module_id)
        if not any(c["module"] == module_id for c in orch.store.cards(learner)):
            orch.start_module(learner, module_id)
            if not offline:
                orch.pregenerate_lessons(module_id)
        return RedirectResponse("/", status_code=303)

    # --- lessons ----------------------------------------------------------
    @app.get("/lesson/{module_id}/{index}", response_class=HTMLResponse)
    def lesson(request: Request, module_id: str, index: int, regenerate: int = 0):
        mod = bundle.course.module(module_id)
        if not 0 <= index < len(mod.concepts):
            return render(request, "message.html", title="No such lesson", text="")
        concept = mod.concepts[index]
        les = orch.get_lesson(module_id, concept, regenerate=bool(regenerate))
        studied = any(e["payload"].get("concept") == concept and e["payload"].get("module") == module_id for e in orch.store.events(learner, "lesson_done", limit=1000))
        feedback = orch.store.chat(learner, f"lesson:{module_id}:{index}")
        primary = mod.texts[0] if mod.texts else None
        return render(request, "lesson.html", m=mod, index=index, concept=concept, lesson=les, body=les["body"], studied=studied,
                      feedback=feedback, primary=primary, prev=index - 1 if index > 0 else None, nxt=index + 1 if index + 1 < len(mod.concepts) else None)

    @app.post("/lesson/{module_id}/{index}/check")
    def lesson_check(module_id: str, index: int, answer: str = Form(...)):
        mod = bundle.course.module(module_id)
        concept = mod.concepts[index]
        reply = orch.check_lesson_answer(learner, module_id, concept, answer)
        key = f"lesson:{module_id}:{index}"
        orch.store.add_chat(learner, key, "user", answer)
        orch.store.add_chat(learner, key, "assistant", reply.reply)
        return RedirectResponse(f"/lesson/{module_id}/{index}#check", status_code=303)

    @app.post("/lesson/{module_id}/{index}/done")
    def lesson_done(module_id: str, index: int):
        mod = bundle.course.module(module_id)
        orch.mark_lesson_done(learner, module_id, mod.concepts[index])
        mark_done(orch.store, learner, f"lesson:{index}")
        return RedirectResponse("/", status_code=303)

    @app.get("/progress", response_class=HTMLResponse)
    def dashboard(request: Request):
        if orch.store.learner(learner) is None:
            return render(request, "enroll.html")
        status = orch.status(learner)
        phase = next(p for p in bundle.course.phases if status["phase"].startswith(p.id))
        gate = bundle.course.gate_after(phase.id)
        exams = []
        if gate:
            for eid in gate.exams:
                spec = bundle.course.exam(eid)
                ok, why = can_attempt(orch.store, learner, spec)
                exams.append({"spec": spec, "can": ok, "why": why, "passed": orch.store.exam_passed(learner, eid)})
        return render(request, "dashboard.html", status=status, phase=phase, gate=gate, exams=exams)

    @app.post("/enroll")
    def enroll():
        if orch.store.learner(learner) is None:
            orch.enroll(learner)
        return RedirectResponse("/", status_code=303)

    # --- curriculum and modules ----------------------------------------
    @app.get("/curriculum", response_class=HTMLResponse)
    def curriculum(request: Request):
        counts = {m.id: len(bundle.problems_for(m.id)) for m in bundle.course.all_modules()}
        return render(request, "curriculum.html", counts=counts)

    @app.get("/module/{module_id}", response_class=HTMLResponse)
    def module(request: Request, module_id: str):
        m = bundle.course.module(module_id)
        probs = bundle.problems_for(module_id)
        done = {p.id for p in probs if orch.store.has_submitted(learner, p.id)}
        started = any(c["module"] == module_id for c in orch.store.cards(learner))
        studied = {e["payload"].get("concept") for e in orch.store.events(learner, "lesson_done", limit=1000) if e["payload"].get("module") == module_id}
        have = orch.store.lessons_for(module_id)
        return render(request, "module.html", m=m, probs=probs, done=done, started=started, studied=studied, have=have)

    @app.post("/module/{module_id}/start")
    def module_start(module_id: str):
        orch.start_module(learner, module_id)
        if not offline:
            orch.pregenerate_lessons(module_id)
        return RedirectResponse(f"/module/{module_id}", status_code=303)

    # --- problems --------------------------------------------------------
    @app.get("/problem/{problem_id}", response_class=HTMLResponse)
    def problem(request: Request, problem_id: str):
        p = orch.problem(problem_id)
        subs = orch.store.submissions(learner, problem_id)
        grades = [(s, orch.store.grade(s.id)) for s in subs]
        try:
            sol = orch.reveal_solution(learner, problem_id)
        except SolutionLocked:
            sol = None
        return render(request, "problem.html", p=p, grades=grades, solution=sol)

    @app.post("/problem/{problem_id}/submit")
    def problem_submit(problem_id: str, content: str = Form(...)):
        orch.submit(learner, problem_id, content)
        return RedirectResponse(f"/problem/{problem_id}", status_code=303)

    # --- tutor -----------------------------------------------------------
    @app.get("/tutor/{module_id}", response_class=HTMLResponse)
    def tutor(request: Request, module_id: str, problem: str | None = None):
        key = f"tutor:{module_id}:{problem or ''}"
        return render(request, "tutor.html", m=bundle.course.module(module_id), problem=problem, history=orch.store.chat(learner, key))

    @app.post("/tutor/{module_id}")
    def tutor_post(module_id: str, message: str = Form(...), problem: str = Form("")):
        orch.chat_turn(learner, f"tutor:{module_id}:{problem}", module_id, message, problem or None)
        return RedirectResponse(f"/tutor/{module_id}" + (f"?problem={problem}" if problem else ""), status_code=303)

    @app.post("/tutor/{module_id}/clear")
    def tutor_clear(module_id: str, problem: str = Form("")):
        orch.store.clear_chat(learner, f"tutor:{module_id}:{problem}")
        return RedirectResponse(f"/tutor/{module_id}" + (f"?problem={problem}" if problem else ""), status_code=303)

    # --- written exams -----------------------------------------------------
    @app.post("/exam/{exam_id}/start")
    def exam_start(request: Request, exam_id: str):
        try:
            att_id, _ = orch.start_written(learner, exam_id)
        except GateError as exc:
            return render(request, "message.html", title="Cannot start exam", text=str(exc))
        return RedirectResponse(f"/attempt/{att_id}", status_code=303)

    @app.get("/attempt/{attempt_id}", response_class=HTMLResponse)
    def attempt(request: Request, attempt_id: int):
        att = next((a for a in orch.store.exam_attempts(learner) if a.id == attempt_id), None)
        if att is None:
            return render(request, "message.html", title="No such attempt", text="")
        probs = [orch.problem(pid) for pid in att.problem_ids]
        spec = bundle.fixed_exams.get(att.exam_id) or bundle.course.exam(att.exam_id)
        results = None
        if att.finished_at is not None:
            results = []
            for s in orch.store.submissions(learner):
                if s.exam_attempt_id == attempt_id:
                    results.append((s, orch.store.grade(s.id)))
        return render(request, "attempt.html", att=att, probs=probs, spec=spec, results=results)

    @app.post("/attempt/{attempt_id}/submit")
    async def attempt_submit(request: Request, attempt_id: int):
        form = await request.form()
        answers = {k[len("ans_"):]: str(v) for k, v in form.items() if k.startswith("ans_")}
        try:
            orch.submit_written(learner, attempt_id, answers)
        except GateError as exc:
            return render(request, "message.html", title="Cannot submit", text=str(exc))
        return RedirectResponse(f"/attempt/{attempt_id}", status_code=303)

    # --- oral exams --------------------------------------------------------
    @app.get("/oral/{exam_id}", response_class=HTMLResponse)
    def oral(request: Request, exam_id: str):
        sess = orch.resume_oral(learner, exam_id)
        if sess is None:
            try:
                sess = orch.start_oral(learner, exam_id)
            except GateError as exc:
                return render(request, "message.html", title="Cannot start oral", text=str(exc))
        return render(request, "oral.html", sess=sess, spec=bundle.course.exam(exam_id))

    @app.post("/oral/{exam_id}")
    def oral_post(request: Request, exam_id: str, answer: str = Form(...)):
        sess = orch.resume_oral(learner, exam_id)
        if sess is None:
            return RedirectResponse(f"/oral/{exam_id}", status_code=303)
        turn = orch.oral_answer(sess, answer)
        if turn.done:
            return render(request, "oral_done.html", turn=turn, spec=bundle.course.exam(exam_id))
        return RedirectResponse(f"/oral/{exam_id}", status_code=303)

    @app.post("/advance")
    def advance(request: Request):
        ok, msg = orch.advance(learner)
        return render(request, "message.html", title="Advanced" if ok else "Not advanced", text=msg)

    # --- retrieval, plan, lecture, advisor --------------------------------
    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request):
        due = orch.due(learner)
        return render(request, "review.html", card=due[0] if due else None, remaining=len(due))

    @app.post("/review/{card_id}")
    def review_post(card_id: str, grade: int = Form(...)):
        orch.review_card(learner, card_id, max(0, min(5, grade)))
        return RedirectResponse("/review", status_code=303)

    @app.get("/plan", response_class=HTMLResponse)
    def plan(request: Request):
        return render(request, "plan.html", plan=orch.plan(learner))

    @app.get("/lecture", response_class=HTMLResponse)
    def lecture(request: Request):
        return render(request, "lecture.html", review=None, modules=bundle.course.all_modules())

    @app.post("/lecture", response_class=HTMLResponse)
    def lecture_post(request: Request, module_id: str = Form(...), text: str = Form(...)):
        rev, ok = orch.critique(learner, module_id, text)
        return render(request, "lecture.html", review=rev, verified=ok, modules=bundle.course.all_modules())

    @app.get("/advisor", response_class=HTMLResponse)
    def advisor(request: Request):
        return render(request, "advisor.html", history=orch.store.chat(learner, "advisor"))

    @app.post("/advisor")
    def advisor_post(message: str = Form(...)):
        orch.chat_turn(learner, "advisor", None, message)
        return RedirectResponse("/advisor", status_code=303)

    @app.post("/anchors")
    def anchors(a1: str = Form(...), a2: str = Form(...)):
        orch.store.set_anchors(learner, [a1, a2])
        return RedirectResponse("/", status_code=303)

    @app.get("/kit", response_class=HTMLResponse)
    def kit(request: Request):
        return render(request, "kit.html", kit=bundle.lab_kit)

    return app


def main() -> None:
    import uvicorn

    uvicorn.run(create_app(), host=os.environ.get("HOST", "0.0.0.0"), port=int(os.environ.get("PORT", "8000")))


if __name__ == "__main__":
    main()
