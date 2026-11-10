"""The contract matrix: each numbered section of the forecast and settlement contract, the checks that hold the code to it (found by name in the
repository), or the stated reason that none exists. A section the evidence file does not mention, or a check that cannot be found, is an error."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "contract_matrix.py"

CONTRACT = """# Contract

preface

## 0. Principles

text

## 1. Common forecast record

text with ## 9. not a heading

### 1.1 sub

## 2. Contract D: directional forecast
"""


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("contract_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["contract_matrix"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "forecast_checks.py").write_text("def check_record():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_the_sections_are_the_numbered_headings_in_order() -> None:
    sections = load().read_sections(CONTRACT)
    assert [(s.number, s.title) for s in sections] == [
        ("0", "Principles"),
        ("1", "Common forecast record"),
        ("2", "Contract D: directional forecast"),
    ]


def test_each_section_gets_one_honest_verdict(repo: Path) -> None:
    module = load()
    sections = module.read_sections(CONTRACT)
    evidence = {
        "0": ["forecast_checks"],
        "1": {"verdict": "NOT BUILT", "reason": "no module yet"},
        "2": ["forecast_checks.check_gone"],
    }
    judged = module.judge(sections, evidence, repo)
    assert [j.verdict for j in judged] == ["CHECKS FOUND", "NOT BUILT", "CHECK NOT FOUND"]
    assert judged[1].reason == "no module yet" and judged[2].missing == ("forecast_checks.check_gone",)
    unnamed = module.judge(sections, {"0": ["forecast_checks"]}, repo)
    assert [j.verdict for j in unnamed] == ["CHECKS FOUND", "NO CHECK NAMED", "NO CHECK NAMED"]


def test_bad_evidence_is_refused(repo: Path) -> None:
    module = load()
    sections = module.read_sections(CONTRACT)
    with pytest.raises(module.ContractError, match="not a section"):
        module.judge(sections, {"7": ["forecast_checks"]}, repo)
    with pytest.raises(module.ContractError, match="a reason"):
        module.judge(sections, {"0": {"verdict": "NOT BUILT", "reason": ""}}, repo)
    with pytest.raises(module.ContractError, match="verdict"):
        module.judge(sections, {"0": {"verdict": "PASS", "reason": "x"}}, repo)
    with pytest.raises(module.ContractError, match="a check is a name"):
        module.judge(sections, {"0": [""]}, repo)


def test_the_page_says_what_is_not_covered(repo: Path) -> None:
    module = load()
    sections = module.read_sections(CONTRACT)
    page = module.render(module.judge(sections, {"0": ["forecast_checks"], "1": {"verdict": "NOT BUILT", "reason": "no module yet"}}, repo))
    assert "| 0 | Principles |" in page and "no module yet" in page and "NO CHECK NAMED: 1" in page
    assert "A check that exists is not a check that passes" in page


def test_every_section_of_the_real_contract_is_accounted_for() -> None:
    module = load()
    root = SCRIPT.parents[1]
    sections = module.read_sections(module.CONTRACT.read_text(encoding="utf-8"))
    assert len(sections) >= 50
    evidence = json.loads((root / "docs" / "contract_evidence.json").read_text(encoding="utf-8"))
    judged = module.judge(sections, evidence, root)
    bad = [(j.section.number, j.verdict, j.missing) for j in judged if j.verdict in ("CHECK NOT FOUND", "NO CHECK NAMED")]
    assert not bad, bad


def test_the_published_page_is_what_the_files_make() -> None:
    module = load()
    root = SCRIPT.parents[1]
    sections = module.read_sections(module.CONTRACT.read_text(encoding="utf-8"))
    evidence = json.loads((root / "docs" / "contract_evidence.json").read_text(encoding="utf-8"))
    assert module.render(module.judge(sections, evidence, root)) == module.DEFAULT_OUT.read_text(encoding="utf-8"), (
        "run scripts/contract_matrix.py"
    )
