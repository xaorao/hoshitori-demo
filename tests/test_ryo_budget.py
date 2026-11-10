"""What a monthly RYO allowance pays for: the checks pass on the real module and every small change of its code is noticed or harmless."""

from __future__ import annotations

import pytest

from hoshitori import ryo_budget
from tests.mutation_sweep import survivors
from tests.ryo_budget_checks import SCENARIOS, failures


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](ryo_budget) == []


def test_real_module_passes_every_check() -> None:
    assert failures(ryo_budget) == []


EQUIVALENT: dict[str, str] = {}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.ryo_budget", "tests.ryo_budget_checks")
    assert total > 30, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
