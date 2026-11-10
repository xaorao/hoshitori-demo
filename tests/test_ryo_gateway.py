"""RYO attached to the gateway: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

from pathlib import Path

import pytest

from hoshitori import ryo_gateway
from tests.mutation_sweep import survivors
from tests.ryo_gateway_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str, tmp_path: Path) -> None:
    assert SCENARIOS[name](ryo_gateway, tmp_path) == []


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_gateway) == []


EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ryo_gateway", "tests.ryo_gateway_checks")
    assert total > 20, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
