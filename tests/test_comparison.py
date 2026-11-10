"""Comparing forecasters: the checks pass on the real module and every small change of its code is noticed or listed as harmless."""

from __future__ import annotations

import pytest

from hoshitori import comparison
from tests.comparison_checks import SCENARIOS, failures
from tests.mutation_sweep import survivors


@pytest.mark.parametrize("name", SCENARIOS)
def test_scenario(name: str) -> None:
    assert SCENARIOS[name](comparison) == []


def test_real_module_passes_every_check() -> None:
    assert failures(comparison) == []


_ZERO = "differs only when an interval's bound is exactly zero, which these float sums do not produce: untested, not shown harmless"
EQUIVALENT: dict[str, str] = {
    "compare|Gt->GtE|last.lower > 0|0": _ZERO,
    "compare|Gt->GtE|s.lower > 0|0": _ZERO,
    "compare|Lt->LtE|last.upper < 0|0": _ZERO,
    "compare|Lt->LtE|s.upper < 0|0": _ZERO,
}


def test_every_change_of_the_code_is_noticed_or_harmless() -> None:
    alive, total = survivors("hoshitori.comparison", "tests.comparison_checks")
    assert total > 40, f"only {total} places to change: the sweep is not looking at the module"
    assert sorted(alive) == sorted(EQUIVALENT), (
        f"unexplained survivors: {sorted(set(alive) - set(EQUIVALENT))}; stale entries: {sorted(set(EQUIVALENT) - set(alive))}"
    )
