"""The rubric matrix: each line of the organisers' published rubric, the page that bears on it, and the checks that hold that page to what it
says, found by name in the repository. A line with no check, a check that cannot be found, or a line the matrix does not know is an error."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "rubric_matrix.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("rubric_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["rubric_matrix"] = module
    spec.loader.exec_module(module)
    return module


JUDGES = """\
export const JUDGE_MAP: JudgeLine[] = [
  {
    group: "common",
    line: "Combining sources",
    points: 20,
    where: { page: "calls", cycle: null },
    look: "Open any call.",
    limit: "No RYO evidence yet.",
  },
  {
    group: "agents",
    line: "Real thinking",
    points: 25,
    where: { page: "desk" },
    look: "A run.",
    limit: "Flat position only.",
  },
];
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "collect_checks.py").write_text("def check_all():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "desk_checks.py").write_text("def check_checks():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_the_lines_are_read_from_the_judges_map() -> None:
    lines = load().read_lines(JUDGES)
    assert [(x.group, x.line, x.points, x.page, x.limit) for x in lines] == [
        ("common", "Combining sources", 20, "calls", "No RYO evidence yet."),
        ("agents", "Real thinking", 25, "desk", "Flat position only."),
    ]


def test_every_line_needs_evidence_that_exists(repo: Path) -> None:
    module = load()
    lines = module.read_lines(JUDGES)
    good = {"Combining sources": ["collect_checks"], "Real thinking": ["desk_checks.check_checks"]}
    assert [r.verdict for r in module.judge(lines, good, repo)] == ["CHECKS FOUND", "CHECKS FOUND"]
    missing = {"Combining sources": ["collect_checks"], "Real thinking": ["desk_checks.check_gone"]}
    judged = module.judge(lines, missing, repo)
    assert [r.verdict for r in judged] == ["CHECKS FOUND", "CHECK NOT FOUND"] and judged[1].missing == ("desk_checks.check_gone",)
    bare = {"Combining sources": ["collect_checks"]}
    assert [r.verdict for r in module.judge(lines, bare, repo)] == ["CHECKS FOUND", "NO CHECK NAMED"]
    with pytest.raises(module.RubricError, match="not a line"):
        module.judge(lines, {**good, "Making tea": ["collect_checks"]}, repo)
    with pytest.raises(module.RubricError, match="a check is a name"):
        module.judge(lines, {"Combining sources": [""], "Real thinking": ["desk_checks"]}, repo)


def test_the_page_names_each_line_and_what_it_does_not_show(repo: Path) -> None:
    module = load()
    lines = module.read_lines(JUDGES)
    page = module.render(module.judge(lines, {"Combining sources": ["collect_checks"], "Real thinking": ["desk_checks"]}, repo))
    assert "| Combining sources |" in page and "| Real thinking |" in page
    assert "No RYO evidence yet." in page and "Flat position only." in page
    assert "A check that exists is not a check that passes" in page


def test_the_real_matrix_has_evidence_for_every_published_line() -> None:
    module = load()
    root = SCRIPT.parents[1]
    lines = module.read_lines((root / "web" / "src" / "judges.ts").read_text(encoding="utf-8"))
    evidence = json.loads((root / "docs" / "rubric_evidence.json").read_text(encoding="utf-8"))
    judged = module.judge(lines, evidence, root)
    assert [r.verdict for r in judged] == ["CHECKS FOUND"] * len(lines), [(r.line.line, r.verdict, r.missing) for r in judged]
    totals: dict[str, int] = {}
    for x in lines:
        totals[x.group] = totals.get(x.group, 0) + x.points
    assert totals == {"common": 40, "agents": 60, "interfaces": 60, "skills": 60}


def test_the_published_page_is_what_the_files_make() -> None:
    module = load()
    root = SCRIPT.parents[1]
    out = module.DEFAULT_OUT
    if not out.exists():
        pytest.skip("the specification folder is not part of this checkout")
    lines = module.read_lines((root / "web" / "src" / "judges.ts").read_text(encoding="utf-8"))
    evidence = json.loads((root / "docs" / "rubric_evidence.json").read_text(encoding="utf-8"))
    assert module.render(module.judge(lines, evidence, root)) == out.read_text(encoding="utf-8"), "run scripts/rubric_matrix.py"
