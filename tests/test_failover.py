"""Fenced promotion against its scenarios."""

from __future__ import annotations

import types
from pathlib import Path

import pytest

from hoshitori import cell, failover, ledger
from tests.failover_checks import SCENARIOS, failures

REAL = types.SimpleNamespace(ledger=ledger, failover=failover, cell=cell)


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](REAL, tmp_path) == []


def test_all_scenarios_together() -> None:
    assert failures(REAL) == []
