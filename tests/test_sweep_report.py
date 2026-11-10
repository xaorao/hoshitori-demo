"""The sweep report: the page is made from the log, and the published page is the table of its own data."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import sweep_report
from tests.sweep_report_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](sweep_report, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(sweep_report) == []


def test_the_published_page_was_made_on_the_code_as_it_is_now() -> None:
    root = Path(__file__).resolve().parents[1]
    page = root / "docs" / "SWEEP.md"
    if not page.exists():
        pytest.skip("docs/SWEEP.md is made once, from a run of every sweep, as the last step; until then there is no figure to be stale")
    assert sweep_report.stale(sweep_report.entries_from(page.read_text(encoding="utf-8")), root) == []
