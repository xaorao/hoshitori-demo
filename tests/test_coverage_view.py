"""The coverage view: the checks pass on the real module, and the real matrices are read."""

from __future__ import annotations

from pathlib import Path

from hoshitori import coverage_view
from tests.coverage_view_checks import failures
from tests.mutation_sweep import survivors

ROOT = Path(__file__).resolve().parents[1]


def test_real_module_passes_every_check() -> None:
    assert failures(coverage_view) == []


def test_the_published_matrices_are_read() -> None:
    specs = ROOT.parent / "research" / "architecture"
    if not (specs / "CONTRACT_MATRIX.md").exists():
        specs = ROOT / "docs" / "specs"
    got = coverage_view.read(
        (specs / "RUBRIC_MATRIX.md").read_text(encoding="utf-8"), (specs / "CONTRACT_MATRIX.md").read_text(encoding="utf-8")
    )
    assert got.contract_total >= 50 and got.rubric_total == 12
    assert all(g.reason for g in got.gaps)


# Changes that no check can notice because the code does the same thing either way. Each was read; the list must match the sweep exactly.
EQUIVALENT: dict[str, str] = {
    "_contract|str+~|'|'|1": "the second '|' is the one stripped from both ends of a row, and a row neither starts nor ends with a tilde, so stripping '|~' takes off the same characters",
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.coverage_view", "tests.coverage_view_checks")
    assert total > 60, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
