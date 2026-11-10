"""The proof matrix: every invariant of ``architecture/INVARIANTS.md`` is shown with the evidence it names, and a claim of "tested" counts
only when a named test or check can be found in the repository."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "proof_matrix.py"


def load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("proof_matrix", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["proof_matrix"] = module
    spec.loader.exec_module(module)
    return module


TABLE = """\
## 2. Ledger

| ID | Invariant | Check | Status |
|---|---|---|---|
| L-1 | seq is gapless | PROP | **tested** (product): `test_here`, `check_there.invariants` |
| L-2 | a closed segment matches | UNIT | **tested** (product): `test_missing_name` |
| L-3 | a model only | MODEL | **tested (model)** |
| L-4 | nothing yet | PROP | to build |
| L-7 | no module | PROP | not built: the module that would hold it does not exist |
| L-8 | a name with a unit | PROP | tested: `test_here`; the rest is not built |
| L-9 | partly, with a test | PROP | partly: `test_here` checks one half; the other half is not tested |
| L-10 | partly, with a test that is gone | PROP | partly: `test_gone` checks one half |
| L-6 | an experiment | PROP | E1: 146 vectors, both languages agree |
| L-5 | partly | PROP | partly tested (a malformed time crashed one verifier and was fixed); full fuzzing to build |

## 3. Two columns

| ID | Invariant |
|---|---|
| S-1 | designed only |
"""


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_here.py").write_text("def test_here():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests" / "check_there.py").write_text("def invariants():\n    pass\n", encoding="utf-8")
    return tmp_path


def test_rows_are_read_with_or_without_a_status_column() -> None:
    rows = load().read_rows(TABLE)
    assert [(r.ident, r.status) for r in rows] == [
        ("L-1", "**tested** (product): `test_here`, `check_there.invariants`"),
        ("L-2", "**tested** (product): `test_missing_name`"),
        ("L-3", "**tested (model)**"),
        ("L-4", "to build"),
        ("L-7", "not built: the module that would hold it does not exist"),
        ("L-8", "tested: `test_here`; the rest is not built"),
        ("L-9", "partly: `test_here` checks one half; the other half is not tested"),
        ("L-10", "partly: `test_gone` checks one half"),
        ("L-6", "E1: 146 vectors, both languages agree"),
        ("L-5", "partly tested (a malformed time crashed one verifier and was fixed); full fuzzing to build"),
        ("S-1", ""),
    ]


def test_a_named_test_counts_only_if_it_exists(repo: Path) -> None:
    module = load()
    verdicts = {r.ident: module.judge(r, repo) for r in module.read_rows(TABLE)}
    assert verdicts["L-1"].verdict == "NAMED TESTS FOUND" and verdicts["L-1"].found == ("test_here", "invariants")
    assert verdicts["L-2"].verdict == "NAMED TESTS NOT FOUND" and verdicts["L-2"].missing == ("test_missing_name",)
    assert verdicts["L-3"].verdict == "RESEARCH ONLY"
    assert verdicts["L-4"].verdict == "NO TEST NAMED"
    assert verdicts["L-7"].verdict == "NOT BUILT"  # a property of a part that does not exist has no test, and says so
    assert verdicts["L-8"].verdict == "NAMED TESTS FOUND"  # the name is what counts when the row names a test
    assert verdicts["L-9"].verdict == "PARTLY, WITH A NAMED GAP" and verdicts["L-9"].found == (
        "test_here",
    )  # a test does not make a half a whole
    assert verdicts["L-10"].verdict == "NAMED TESTS NOT FOUND"  # but a test that is not there is worse than a gap
    assert verdicts["L-5"].verdict == "PARTLY, WITH A NAMED GAP"
    assert verdicts["L-6"].verdict == "RESEARCH ONLY"
    assert verdicts["S-1"].verdict == "NO TEST NAMED"


def test_the_page_lists_every_row_and_counts_each_verdict(repo: Path) -> None:
    module = load()
    page = module.render(module.read_rows(TABLE), repo)
    assert "| L-1 |" in page and "| S-1 |" in page
    assert (
        "NAMED TESTS FOUND: 2" in page
        and "NO TEST NAMED: 2" in page
        and "NAMED TESTS NOT FOUND: 2" in page
        and "NOT BUILT: 1" in page
        and "PARTLY, WITH A NAMED GAP: 2" in page
    )
    assert "A test that exists is not a test that passes" in page  # said once, plainly


def test_the_published_matrix_is_what_the_invariants_file_makes() -> None:
    module = load()
    if not module.DEFAULT_IN.exists() or not module.DEFAULT_OUT.exists():
        pytest.skip("the research folder is not beside this repository")
    made = module.render(module.read_rows(module.DEFAULT_IN.read_text(encoding="utf-8")), module.REPO)
    assert made == module.DEFAULT_OUT.read_text(encoding="utf-8"), (
        "run scripts/proof_matrix.py: the matrix is older than INVARIANTS.md or the tests"
    )
