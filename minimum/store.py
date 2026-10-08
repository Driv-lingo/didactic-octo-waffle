"""The learner model: a SQLite record of everything the learner has done.

This is the only persistent state in the system. Agents never share memory
directly; they read views of this store that the orchestrator builds for them.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .models import EdgeNote, ExamAttempt, Grade, Submission

SCHEMA = """
CREATE TABLE IF NOT EXISTS learner (
    name TEXT PRIMARY KEY,
    course_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    current_phase TEXT NOT NULL,
    anchors TEXT NOT NULL DEFAULT '[]'
);
CREATE TABLE IF NOT EXISTS submission (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner TEXT NOT NULL,
    problem_id TEXT NOT NULL,
    content TEXT NOT NULL,
    submitted_at TEXT NOT NULL,
    exam_attempt_id INTEGER
);
CREATE TABLE IF NOT EXISTS grade (
    submission_id INTEGER PRIMARY KEY,
    score REAL NOT NULL,
    max_score REAL NOT NULL,
    deterministic INTEGER NOT NULL,
    feedback TEXT NOT NULL,
    rubric_breakdown TEXT NOT NULL,
    verified INTEGER NOT NULL,
    verifier_notes TEXT NOT NULL,
    graded_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS exam_attempt (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner TEXT NOT NULL,
    exam_id TEXT NOT NULL,
    problem_ids TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    score REAL,
    max_score REAL,
    passed INTEGER
);
CREATE TABLE IF NOT EXISTS edge_note (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner TEXT NOT NULL,
    module TEXT NOT NULL,
    concept TEXT NOT NULL,
    note TEXT NOT NULL,
    source TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_card (
    learner TEXT NOT NULL,
    card_id TEXT NOT NULL,
    module TEXT NOT NULL,
    prompt TEXT NOT NULL,
    ease REAL NOT NULL,
    interval_days REAL NOT NULL,
    repetitions INTEGER NOT NULL,
    due_at TEXT NOT NULL,
    last_grade INTEGER,
    PRIMARY KEY (learner, card_id)
);
CREATE TABLE IF NOT EXISTS event (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    learner TEXT NOT NULL,
    kind TEXT NOT NULL,
    payload TEXT NOT NULL,
    at TEXT NOT NULL
);
"""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _dt(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s) if s else None


class Store:
    def __init__(self, path: str | Path = ":memory:"):
        self.path = str(path)
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.on_commit = None  # optional callback, invoked after every commit

    def close(self) -> None:
        self.conn.close()

    def _commit(self) -> None:
        self.conn.commit()
        if self.on_commit is not None:
            self.on_commit()

    def snapshot(self, dest_path: str | Path) -> None:
        """Write a consistent copy of the database to dest_path (SQLite backup API)."""
        dest = sqlite3.connect(str(dest_path))
        try:
            with dest:
                self.conn.backup(dest)
        finally:
            dest.close()

    # --- learner --------------------------------------------------------
    def create_learner(self, name: str, course_id: str, first_phase: str) -> None:
        self.conn.execute(
            "INSERT INTO learner(name, course_id, started_at, current_phase) VALUES (?,?,?,?)",
            (name, course_id, _iso(_now()), first_phase),
        )
        self._commit()

    def learner(self, name: str) -> dict | None:
        row = self.conn.execute("SELECT * FROM learner WHERE name=?", (name,)).fetchone()
        if row is None:
            return None
        d = dict(row)
        d["anchors"] = json.loads(d["anchors"])
        d["started_at"] = _dt(d["started_at"])
        return d

    def set_phase(self, name: str, phase_id: str) -> None:
        self.conn.execute("UPDATE learner SET current_phase=? WHERE name=?", (phase_id, name))
        self._commit()

    def set_anchors(self, name: str, anchors: list[str]) -> None:
        self.conn.execute(
            "UPDATE learner SET anchors=? WHERE name=?", (json.dumps(anchors), name)
        )
        self._commit()

    # --- submissions and grades ------------------------------------------
    def add_submission(self, sub: Submission) -> int:
        cur = self.conn.execute(
            "INSERT INTO submission(learner, problem_id, content, submitted_at, exam_attempt_id)"
            " VALUES (?,?,?,?,?)",
            (sub.learner, sub.problem_id, sub.content, _iso(sub.submitted_at), sub.exam_attempt_id),
        )
        self._commit()
        return int(cur.lastrowid)

    def submissions(self, learner: str, problem_id: str | None = None) -> list[Submission]:
        q = "SELECT * FROM submission WHERE learner=?"
        args: list = [learner]
        if problem_id:
            q += " AND problem_id=?"
            args.append(problem_id)
        rows = self.conn.execute(q + " ORDER BY id", args).fetchall()
        return [
            Submission(
                id=r["id"],
                learner=r["learner"],
                problem_id=r["problem_id"],
                content=r["content"],
                submitted_at=_dt(r["submitted_at"]),
                exam_attempt_id=r["exam_attempt_id"],
            )
            for r in rows
        ]

    def has_submitted(self, learner: str, problem_id: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM submission WHERE learner=? AND problem_id=? LIMIT 1",
            (learner, problem_id),
        ).fetchone()
        return row is not None

    def add_grade(self, g: Grade) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO grade VALUES (?,?,?,?,?,?,?,?,?)",
            (
                g.submission_id,
                g.score,
                g.max_score,
                int(g.deterministic),
                g.feedback,
                json.dumps(g.rubric_breakdown),
                int(g.verified),
                g.verifier_notes,
                _iso(g.graded_at),
            ),
        )
        self._commit()

    def grade(self, submission_id: int) -> Grade | None:
        r = self.conn.execute("SELECT * FROM grade WHERE submission_id=?", (submission_id,)).fetchone()
        if r is None:
            return None
        return Grade(
            submission_id=r["submission_id"],
            score=r["score"],
            max_score=r["max_score"],
            deterministic=bool(r["deterministic"]),
            feedback=r["feedback"],
            rubric_breakdown=json.loads(r["rubric_breakdown"]),
            verified=bool(r["verified"]),
            verifier_notes=r["verifier_notes"],
            graded_at=_dt(r["graded_at"]),
        )

    def grades_for_module(self, learner: str, problem_ids: list[str]) -> list[tuple[str, Grade]]:
        out = []
        for s in self.submissions(learner):
            if s.problem_id in problem_ids and s.exam_attempt_id is None:
                g = self.grade(s.id)
                if g:
                    out.append((s.problem_id, g))
        return out

    # --- exams -----------------------------------------------------------
    def start_exam(self, att: ExamAttempt) -> int:
        cur = self.conn.execute(
            "INSERT INTO exam_attempt(learner, exam_id, problem_ids, started_at) VALUES (?,?,?,?)",
            (att.learner, att.exam_id, json.dumps(att.problem_ids), _iso(att.started_at)),
        )
        self._commit()
        return int(cur.lastrowid)

    def finish_exam(self, attempt_id: int, score: float, max_score: float, passed: bool) -> None:
        self.conn.execute(
            "UPDATE exam_attempt SET finished_at=?, score=?, max_score=?, passed=? WHERE id=?",
            (_iso(_now()), score, max_score, int(passed), attempt_id),
        )
        self._commit()

    def exam_attempts(self, learner: str, exam_id: str | None = None) -> list[ExamAttempt]:
        q = "SELECT * FROM exam_attempt WHERE learner=?"
        args: list = [learner]
        if exam_id:
            q += " AND exam_id=?"
            args.append(exam_id)
        rows = self.conn.execute(q + " ORDER BY id", args).fetchall()
        return [
            ExamAttempt(
                id=r["id"],
                learner=r["learner"],
                exam_id=r["exam_id"],
                problem_ids=json.loads(r["problem_ids"]),
                started_at=_dt(r["started_at"]),
                finished_at=_dt(r["finished_at"]),
                score=r["score"],
                max_score=r["max_score"],
                passed=None if r["passed"] is None else bool(r["passed"]),
            )
            for r in rows
        ]

    def exam_passed(self, learner: str, exam_id: str) -> bool:
        return any(a.passed for a in self.exam_attempts(learner, exam_id))

    # --- edge notes ------------------------------------------------------
    def add_edge_note(self, n: EdgeNote) -> None:
        self.conn.execute(
            "INSERT INTO edge_note(learner, module, concept, note, source, created_at) VALUES (?,?,?,?,?,?)",
            (n.learner, n.module, n.concept, n.note, n.source, _iso(n.created_at)),
        )
        self._commit()

    def edge_notes(self, learner: str, module: str | None = None, sources: tuple[str, ...] | None = None) -> list[EdgeNote]:
        q = "SELECT * FROM edge_note WHERE learner=?"
        args: list = [learner]
        if module:
            q += " AND module=?"
            args.append(module)
        if sources:
            q += " AND source IN (%s)" % ",".join("?" * len(sources))
            args.extend(sources)
        rows = self.conn.execute(q + " ORDER BY id", args).fetchall()
        return [
            EdgeNote(
                learner=r["learner"],
                module=r["module"],
                concept=r["concept"],
                note=r["note"],
                source=r["source"],
                created_at=_dt(r["created_at"]),
            )
            for r in rows
        ]

    # --- review cards (spaced retrieval) ----------------------------------
    def upsert_card(self, learner: str, card: dict) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO review_card VALUES (?,?,?,?,?,?,?,?,?)",
            (
                learner,
                card["card_id"],
                card["module"],
                card["prompt"],
                card["ease"],
                card["interval_days"],
                card["repetitions"],
                _iso(card["due_at"]),
                card.get("last_grade"),
            ),
        )
        self._commit()

    def card(self, learner: str, card_id: str) -> dict | None:
        r = self.conn.execute(
            "SELECT * FROM review_card WHERE learner=? AND card_id=?", (learner, card_id)
        ).fetchone()
        return self._card_row(r) if r else None

    def cards(self, learner: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM review_card WHERE learner=? ORDER BY due_at", (learner,)
        ).fetchall()
        return [self._card_row(r) for r in rows]

    def due_cards(self, learner: str, now: datetime | None = None) -> list[dict]:
        now = now or _now()
        return [c for c in self.cards(learner) if c["due_at"] <= now]

    @staticmethod
    def _card_row(r) -> dict:
        d = dict(r)
        d["due_at"] = _dt(d["due_at"])
        return d

    # --- events ----------------------------------------------------------
    def log(self, learner: str, kind: str, payload: dict) -> None:
        self.conn.execute(
            "INSERT INTO event(learner, kind, payload, at) VALUES (?,?,?,?)",
            (learner, kind, json.dumps(payload, default=str), _iso(_now())),
        )
        self._commit()

    def events(self, learner: str, kind: str | None = None, limit: int = 100) -> list[dict]:
        q = "SELECT * FROM event WHERE learner=?"
        args: list = [learner]
        if kind:
            q += " AND kind=?"
            args.append(kind)
        rows = self.conn.execute(q + " ORDER BY id DESC LIMIT ?", args + [limit]).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["payload"] = json.loads(d["payload"])
            d["at"] = _dt(d["at"])
            out.append(d)
        return out

    def last_activity(self, learner: str) -> datetime | None:
        r = self.conn.execute(
            "SELECT at FROM event WHERE learner=? ORDER BY id DESC LIMIT 1", (learner,)
        ).fetchone()
        return _dt(r["at"]) if r else None
