"""The spec matrix: each numbered section of the security, privacy and operations specifications, the checks that hold the code to it (found by
name in the repository), or the stated reason that none exists. A section the evidence file does not mention, a check that cannot be found or a
section the specification does not have is an error."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "spec_matrix.py"

ONE = """# Spec one

intro

## 1. First part

text

## 2. Second part

text with ## 9. not a heading

### 2.1 sub

## 3. Third part
"""
TWO = """# Spec two

## 1. Only part
"""


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("spec_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["spec_matrix"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "alpha_checks.py").write_text("def check_it():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_the_sections_come_with_their_specification() -> None:
    got = load().read_sections({"ONE": ONE, "TWO": TWO})
    assert [(s.spec, s.number, s.title) for s in got] == [
        ("ONE", "1", "First part"),
        ("ONE", "2", "Second part"),
        ("ONE", "3", "Third part"),
        ("TWO", "1", "Only part"),
    ]


def test_each_section_gets_one_honest_verdict(repo: Path) -> None:
    module = load()
    sections = module.read_sections({"ONE": ONE, "TWO": TWO})
    evidence = {
        "ONE 1": ["alpha_checks"],
        "ONE 2": {"checks": ["alpha_checks.check_it", "alpha_checks.gone"]},
        "ONE 3": {"verdict": "NOT BUILT", "reason": "no module yet"},
        "TWO 1": {"checks": ["alpha_checks"], "note": "the second half is not checked"},
    }
    judged = module.judge(sections, evidence, repo)
    assert [j.verdict for j in judged] == ["CHECKS FOUND", "CHECK NOT FOUND", "NOT BUILT", "CHECKS FOUND"]
    assert (
        judged[1].missing == ("alpha_checks.gone",)
        and judged[2].reason == "no module yet"
        and judged[3].note == "the second half is not checked"
    )
    unnamed = module.judge(sections, {"ONE 1": ["alpha_checks"]}, repo)
    assert [j.verdict for j in unnamed] == ["CHECKS FOUND", "NO CHECK NAMED", "NO CHECK NAMED", "NO CHECK NAMED"]


def test_bad_evidence_is_refused(repo: Path) -> None:
    module = load()
    sections = module.read_sections({"ONE": ONE})
    with pytest.raises(module.SpecError, match="not a section"):
        module.judge(sections, {"ONE 7": ["alpha_checks"]}, repo)
    with pytest.raises(module.SpecError, match="a reason"):
        module.judge(sections, {"ONE 1": {"verdict": "NOT TESTED", "reason": ""}}, repo)
    with pytest.raises(module.SpecError, match="verdict"):
        module.judge(sections, {"ONE 1": {"verdict": "PASS", "reason": "x"}}, repo)
    with pytest.raises(module.SpecError, match="a check is a name"):
        module.judge(sections, {"ONE 1": [""]}, repo)
    with pytest.raises(module.SpecError, match="a check is a name"):
        module.judge(sections, {"ONE 1": {"checks": []}}, repo)


def test_the_page_says_what_is_not_covered(repo: Path) -> None:
    module = load()
    sections = module.read_sections({"ONE": ONE})
    evidence = {"ONE 1": {"checks": ["alpha_checks"], "note": "half"}, "ONE 2": {"verdict": "NOT BUILT", "reason": "no module yet"}}
    page = module.render(module.judge(sections, evidence, repo))
    assert "| ONE | 1 | First part |" in page and "no module yet" in page and "half" in page and "NO CHECK NAMED: 1" in page
    assert "A check that exists is not a check that passes" in page


def test_every_section_of_the_real_specifications_is_accounted_for() -> None:
    module = load()
    root = SCRIPT.parents[1]
    sections = module.read_sections(module.read_specs())
    assert {s.spec for s in sections} >= {"SECURITY_MODEL", "PRIVACY_DATA_FLOWS", "OPERATIONS_SLOS", "OPERATIONS_ARCHITECTURE"}
    evidence = json.loads((root / "docs" / "spec_evidence.json").read_text(encoding="utf-8"))
    bad = [
        (j.section.spec, j.section.number, j.verdict, j.missing)
        for j in module.judge(sections, evidence, root)
        if j.verdict in ("CHECK NOT FOUND", "NO CHECK NAMED")
    ]
    assert not bad, bad


def test_the_published_page_is_what_the_files_make() -> None:
    module = load()
    root = SCRIPT.parents[1]
    sections = module.read_sections(module.read_specs())
    evidence = json.loads((root / "docs" / "spec_evidence.json").read_text(encoding="utf-8"))
    assert module.render(module.judge(sections, evidence, root)) == module.DEFAULT_OUT.read_text(encoding="utf-8"), (
        "run scripts/spec_matrix.py"
    )
