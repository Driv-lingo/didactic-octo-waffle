"""Load and validate a course directory.

Layout::

    courses/<id>/course.yaml        the course tree, gates, exams
    courses/<id>/problems/*.yaml    problem bank (list of problems per file)
    courses/<id>/exams/*.yaml       optional fixed exams (selection week etc.)
    courses/<id>/labs/kit.yaml      optional lab kit and protocols
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from .models import Course, Material, Problem


class FixedExam(BaseModel):
    id: str
    title: str
    duration_minutes: int
    pass_mark: float
    problems: list[Problem]
    description: str = ""


class LabKit(BaseModel):
    budget_usd: int
    items: list[dict]
    protocols: list[dict] = Field(default_factory=list)


class CourseBundle(BaseModel):
    root: Path
    course: Course
    problems: dict[str, Problem]
    fixed_exams: dict[str, FixedExam] = Field(default_factory=dict)
    lab_kit: LabKit | None = None

    model_config = {"arbitrary_types_allowed": True}

    def problems_for(self, module_id: str, gate_only: bool = False) -> list[Problem]:
        out = [p for p in self.problems.values() if p.module == module_id]
        if gate_only:
            out = [p for p in out if p.gate_eligible]
        return sorted(out, key=lambda p: p.id)

    def validate_bundle(self) -> list[str]:
        """Return a list of content problems (empty means valid)."""
        errors: list[str] = []
        module_ids = {m.id for m in self.course.all_modules()}
        phase_ids = {p.id for p in self.course.phases}
        for p in self.problems.values():
            if p.module not in module_ids:
                errors.append(f"problem {p.id}: unknown module {p.module}")
        for m in self.course.all_modules():
            for pre in m.prerequisites:
                if pre not in module_ids:
                    errors.append(f"module {m.id}: unknown prerequisite {pre}")
        exam_ids = {e.id for e in self.course.exams} | set(self.fixed_exams)
        for g in self.course.gates:
            if g.after_phase not in phase_ids:
                errors.append(f"gate {g.id}: unknown phase {g.after_phase}")
            for e in g.exams:
                if e not in exam_ids:
                    errors.append(f"gate {g.id}: unknown exam {e}")
        for e in self.course.exams:
            for mid in e.modules:
                if mid not in module_ids:
                    errors.append(f"exam {e.id}: unknown module {mid}")
            pool = [
                p
                for p in self.problems.values()
                if p.module in e.modules and p.gate_eligible and p.difficulty >= e.min_difficulty
            ]
            if len(pool) < e.n_problems:
                errors.append(
                    f"exam {e.id}: needs {e.n_problems} eligible problems, bank has {len(pool)}"
                )
        return errors


def _read_yaml(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_course(root: str | Path) -> CourseBundle:
    root = Path(root)
    course = Course.model_validate(_read_yaml(root / "course.yaml"))
    mats_path = root / "materials.yaml"
    if mats_path.exists():
        mats = _read_yaml(mats_path) or {}
        for m in course.all_modules():
            if m.id in mats:
                m.materials = [Material.model_validate(x) for x in mats[m.id]]
        unknown = set(mats) - {m.id for m in course.all_modules()}
        if unknown:
            raise ValueError(f"materials.yaml names unknown modules: {sorted(unknown)}")
    problems: dict[str, Problem] = {}
    for f in sorted((root / "problems").glob("*.yaml")):
        for raw in _read_yaml(f) or []:
            p = Problem.model_validate(raw)
            if p.id in problems:
                raise ValueError(f"duplicate problem id {p.id} in {f}")
            problems[p.id] = p
    fixed: dict[str, FixedExam] = {}
    exams_dir = root / "exams"
    if exams_dir.exists():
        for f in sorted(exams_dir.glob("*.yaml")):
            e = FixedExam.model_validate(_read_yaml(f))
            fixed[e.id] = e
    kit = None
    kit_path = root / "labs" / "kit.yaml"
    if kit_path.exists():
        kit = LabKit.model_validate(_read_yaml(kit_path))
    return CourseBundle(root=root, course=course, problems=problems, fixed_exams=fixed, lab_kit=kit)
