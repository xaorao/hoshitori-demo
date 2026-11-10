"""The accessibility matrix: each binding criterion of the accessibility specification, the checks that hold the pages to it (found by name in
the repository), or the stated reason that none exists. A criterion the evidence file does not mention, a check that cannot be found or an
unstated absence of a check is an error."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "accessibility_matrix.py"

SPEC = """# Spec

## 1. Binding criteria (WCAG 2.2, read in the text)

| Criterion | Level | What it requires | Where checked |
|---|---|---|---|
| 1.4.3, 1.4.11 contrast | AA | text 4.5:1 | axe |
| 2.4.11 Focus not obscured (min) | AA | never covered | page test |
| 3.3.8 Accessible authentication (min) | AA | no puzzle | design rule |

## 2. Structure decisions
"""


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("accessibility_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["accessibility_matrix"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "web" / "tools").mkdir(parents=True)
    (tmp_path / "web" / "tools" / "page_test.py").write_text("def nav_inconsistencies():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_the_criteria_are_the_rows_of_the_binding_table() -> None:
    got = load().read_criteria(SPEC)
    assert [(c.number, c.title, c.level) for c in got] == [
        ("1.4.3, 1.4.11", "contrast", "AA"),
        ("2.4.11", "Focus not obscured (min)", "AA"),
        ("3.3.8", "Accessible authentication (min)", "AA"),
    ]


def test_each_criterion_gets_one_honest_verdict(repo: Path) -> None:
    module = load()
    criteria = module.read_criteria(SPEC)
    evidence = {
        "1.4.3, 1.4.11": ["page_test"],
        "2.4.11": ["page_test.nav_inconsistencies", "page_test.gone"],
        "3.3.8": {"verdict": "NOT APPLICABLE", "reason": "the product has no sign-in"},
    }
    judged = module.judge(criteria, evidence, repo)
    assert [j.verdict for j in judged] == ["CHECKS FOUND", "CHECK NOT FOUND", "NOT APPLICABLE"]
    assert judged[1].missing == ("page_test.gone",) and judged[2].reason == "the product has no sign-in"
    unnamed = module.judge(criteria, {"1.4.3, 1.4.11": ["page_test"]}, repo)
    assert [j.verdict for j in unnamed] == ["CHECKS FOUND", "NO CHECK NAMED", "NO CHECK NAMED"]


def test_bad_evidence_is_refused(repo: Path) -> None:
    module = load()
    criteria = module.read_criteria(SPEC)
    with pytest.raises(module.AccessibilityError, match="not a criterion"):
        module.judge(criteria, {"9.9.9": ["page_test"]}, repo)
    with pytest.raises(module.AccessibilityError, match="a reason"):
        module.judge(criteria, {"3.3.8": {"verdict": "NOT TESTED", "reason": ""}}, repo)
    with pytest.raises(module.AccessibilityError, match="verdict"):
        module.judge(criteria, {"3.3.8": {"verdict": "PASS", "reason": "x"}}, repo)
    with pytest.raises(module.AccessibilityError, match="a check is a name"):
        module.judge(criteria, {"3.3.8": [""]}, repo)


def test_the_page_says_what_no_test_covers(repo: Path) -> None:
    module = load()
    criteria = module.read_criteria(SPEC)
    page = module.render(
        module.judge(criteria, {"1.4.3, 1.4.11": ["page_test"], "3.3.8": {"verdict": "NOT TESTED", "reason": "no test yet"}}, repo)
    )
    assert "| 1.4.3, 1.4.11 | contrast |" in page and "no test yet" in page and "NO CHECK NAMED: 1" in page
    assert "A check that exists is not a check that passes" in page and "No person using a screen reader" in page


def test_every_criterion_of_the_real_specification_is_accounted_for() -> None:
    module = load()
    root = SCRIPT.parents[1]
    criteria = module.read_criteria(module.SPEC.read_text(encoding="utf-8"))
    assert len(criteria) == 12
    evidence = json.loads((root / "docs" / "accessibility_evidence.json").read_text(encoding="utf-8"))
    bad = [
        (j.criterion.number, j.verdict, j.missing)
        for j in module.judge(criteria, evidence, root)
        if j.verdict in ("CHECK NOT FOUND", "NO CHECK NAMED")
    ]
    assert not bad, bad


def test_the_published_page_is_what_the_files_make() -> None:
    module = load()
    root = SCRIPT.parents[1]
    criteria = module.read_criteria(module.SPEC.read_text(encoding="utf-8"))
    evidence = json.loads((root / "docs" / "accessibility_evidence.json").read_text(encoding="utf-8"))
    assert module.render(module.judge(criteria, evidence, root)) == module.DEFAULT_OUT.read_text(encoding="utf-8"), (
        "run scripts/accessibility_matrix.py"
    )
