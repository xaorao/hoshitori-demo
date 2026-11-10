"""The ledger against its scenarios (normal use, torn writes, corruption, loss of the index, crashes)."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshitori import ledger
from tests.ledger_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](ledger, tmp_path) == []


def test_all_scenarios_together() -> None:
    assert failures(ledger) == []
