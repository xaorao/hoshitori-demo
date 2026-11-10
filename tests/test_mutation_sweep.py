"""The sweep harness: its checks pass on the real harness. The harness is not swept by itself (a sweep of a sweep would run every one of its
many changes against checks that start worker processes); instead ``check_every_verdict`` plants each case the harness must tell apart."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests import mutation_sweep
from tests.mutation_sweep_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](mutation_sweep, tmp_path) == []


def test_real_harness_passes_every_check() -> None:
    assert failures(mutation_sweep) == []
